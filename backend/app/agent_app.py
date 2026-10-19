"""Role-isolated agent worker. Only signed backend assertions can invoke workflows."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import logging
import re
from threading import Lock
from time import monotonic
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.agents.remote_contract import (AgentInvocation, MAX_REQUEST_BYTES, MAX_REQUEST_WIRE_BYTES,
    MAX_AGENT_BYTES, signature, key_probe_outcome, transport_payload, transport_signature,
    decode_transport, wrap_transport)
from app.agents.contracts import StartRetailWorkflow, StartSupplierWorkflow, RetailSelection
from app.contracts import ChatRequest
from app.case_api import invoke
from app.http_contract import install_http_contract
from app.utils.logger import configure_logging
from app.utils.http_logging import HttpLoggingMiddleware

logger = logging.getLogger(__name__)


def log_auth_check(audience, outcome, *, present, length, valid_format):
    # Fixed reason codes and scalar metadata only: never credentials or header/body values.
    logger.info('Agent auth agent=%s outcome=%s signature_present=%s signature_length=%d signature_format_valid=%s',
                audience, outcome, present, length, valid_format)


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)


class RetailStart(StrictPayload):
    request: StartRetailWorkflow
    key: str = Field(min_length=1, max_length=200)


class SupplierStart(StrictPayload):
    request: StartSupplierWorkflow
    key: str = Field(min_length=1, max_length=200)


class Thread(StrictPayload):
    thread_id: UUID


class Selection(Thread):
    selection: RetailSelection


class ModelSelection(Thread):
    model_profile: str = Field(min_length=1, max_length=80)


class Chat(StrictPayload):
    request: ChatRequest


class ReplayGuard:
    """Bounded one-process replay window; never evict an unexpired assertion."""
    def __init__(self, clock=monotonic):
        self.clock, self.lock, self.seen = clock, Lock(), {}

    def claim(self, nonce):
        with self.lock:
            now = self.clock()
            self.seen = {key: expiry for key, expiry in self.seen.items() if expiry > now}
            if nonce in self.seen:
                raise HTTPException(409, 'Assertion already used')
            if len(self.seen) >= 4096:
                raise HTTPException(503, 'Agent request capacity exceeded')
            self.seen[nonce] = now + 40


def dispatch(container, audience, envelope):
    op, payload, session = envelope.operation, envelope.payload, envelope.session
    if op in {'connect_model', 'discover_models'}:
        from app.external_models import ExternalModelInput, ExternalModelDiscoveryInput
        if audience != 'retail' or set(payload) != {'config'}:
            raise HTTPException(403, 'Retail model connection required')
        config = (ExternalModelInput if op == 'connect_model' else ExternalModelDiscoveryInput).model_validate(payload['config'])
        service = container.chat_experience_service
        return invoke(service.connect_external if op == 'connect_model' else service.discover_external, config, session=session)
    # No arbitrary function invocation, graph state, approval assertion, SQL or URL.
    if op == 'chat_catalog':
        if audience != 'retail' or payload:
            raise HTTPException(403, 'Retail catalog operation required')
        return invoke(container.chat_experience_service.catalog, session=session)
    if op in {'chat', 'chat_evaluate'}:
        request = Chat.model_validate(payload).request
        if (request.intent != 'conversation' or (request.role and request.role != session.role)
                or (request.session_id and request.session_id != session.session_id)):
            raise HTTPException(403, 'Chat identity or intent mismatch')
        if audience == 'supplier':
            if op != 'chat':
                raise HTTPException(403, 'Supplier model comparison is unavailable')
            return invoke(container.supplier_conversation_service.respond, request, session=session)
        return invoke(container.chat_experience_service.respond, request, session=session, evaluation=op == 'chat_evaluate')
    service = container.retail_workflow_service if audience == 'retail' else container.supplier_workflow_service
    if op == 'change_model':
        if audience != 'retail':
            raise HTTPException(403, 'Only the retail workflow selects its model')
        body = ModelSelection.model_validate(payload)
        return invoke(service.change_model, str(body.thread_id), body.model_profile, session=session)
    if op == 'start':
        body = (RetailStart if audience == 'retail' else SupplierStart).model_validate(payload)
        return invoke(service.start, body.request, session=session, key=body.key)
    if op == 'select':
        if audience != 'retail':
            raise HTTPException(403, 'Supplier cannot select retail offers')
        body = Selection.model_validate(payload)
        return invoke(service.select, str(body.thread_id), body.selection, session=session)
    body = Thread.model_validate(payload)
    action = service.get if op == 'get' else service.resume
    return invoke(action, str(body.thread_id), session=session)


def create_agent_app(settings, container=None):
    if settings.application_role not in {'retail-agent', 'supplier-agent'}:
        raise ValueError('Agent entry point requires a role-specific application')
    if container is None:
        from app.dependencies import ApplicationContainer
        container = ApplicationContainer(settings)
    audience = settings.application_role.removesuffix('-agent')
    replay = ReplayGuard()

    @asynccontextmanager
    async def lifespan(app):
        configure_logging()
        container.central_logs.start()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            container.close()

    app = FastAPI(title=f'Retail Inventory {audience.title()} Agent', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False
    install_http_contract(app)
    app.add_middleware(HttpLoggingMiddleware, service=f"{audience}-agent")
    app.state.langfuse = container.langfuse
    from app.oci.studio_notification_probe import install_fastapi
    install_fastapi(app, lambda: container.notifications)

    @app.get('/health')
    def health():
        return Response(media_type='application/json')

    @app.get('/ready')
    def ready():
        # Startup probe only. Never queries DB, spends inference tokens or invokes tools.
        return Response(status_code=200 if app.state.ready else 503, media_type='application/json')

    @app.post('/internal/agent/invoke')
    async def agent_invoke(request: Request):
        supplied = request.headers.get('x-agent-signature', '')
        present = 'x-agent-signature' in request.headers
        valid_format = re.fullmatch(r'[0-9a-f]{64}', supplied) is not None
        def auth_event(outcome):
            log_auth_check(audience, outcome, present=present, length=len(supplied), valid_format=valid_format)

        if not present:
            auth_event('missing_signature_header')
            raise HTTPException(401, 'Agent assertion required')
        if not valid_format:
            auth_event('invalid_signature_format')
            raise HTTPException(401, 'Agent assertion required')
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_REQUEST_WIRE_BYTES:
                auth_event('request_too_large')
                raise HTTPException(413, 'Agent request too large')
            chunks.append(chunk)
        raw = b''.join(chunks)
        # Metadata only. The optional probe cannot authenticate or bypass the raw-body MAC.
        logger.info('Agent transport agent=%s direction=inbound body_bytes=%d body_sha256=%s key_probe=%s',
                    audience, len(raw), sha256(raw).hexdigest(),
                    key_probe_outcome(request.headers, settings.agent_service_key))
        try:
            token = transport_payload(raw, allow_legacy=True)
        except ValueError:
            if len(raw) > MAX_REQUEST_BYTES:
                auth_event('request_too_large')
                raise HTTPException(413, 'Agent request too large') from None
            auth_event('invalid_transport')
            raise HTTPException(422, 'Invalid agent transport') from None
        if token is None and len(raw) > MAX_REQUEST_BYTES:
            auth_event('request_too_large')
            raise HTTPException(413, 'Agent request too large')
        expected = (transport_signature(token, settings.agent_service_key) if token is not None
                    else signature(raw, settings.agent_service_key))
        if not hmac.compare_digest(supplied, expected):
            auth_event('signature_mismatch')
            raise HTTPException(401, 'Invalid agent assertion')
        auth_event('signature_verified')
        if token is not None:
            try:
                raw = decode_transport(token, MAX_REQUEST_BYTES)
            except ValueError:
                auth_event('invalid_encoded_payload')
                raise HTTPException(422, 'Invalid agent payload') from None
            logger.info('Agent transport agent=%s direction=decoded protocol=agent-base64-v1 payload_bytes=%d payload_sha256=%s',
                        audience, len(raw), sha256(raw).hexdigest())
        try:
            envelope = AgentInvocation.model_validate_json(raw)
            envelope.check_identity(audience, datetime.now(timezone.utc))
        except PermissionError:
            auth_event('identity_rejected')
            raise HTTPException(403, 'Invalid or expired agent identity') from None
        except ValidationError:
            auth_event('invalid_envelope')
            raise HTTPException(422, 'Invalid agent request') from None
        try:
            replay.claim(envelope.nonce)
        except HTTPException as exc:
            auth_event('replay_rejected' if exc.status_code == 409 else 'replay_capacity_exceeded')
            raise
        auth_event('accepted')
        from app.remote_activity import capture_activity, export_activity
        with capture_activity(envelope.session) as activity:
            try:
                result = await run_in_threadpool(dispatch, container, audience, envelope)
            except ValidationError:
                logger.info('Agent payload agent=%s operation=%s outcome=validation_rejected', audience, envelope.operation)
                raise HTTPException(422, 'Invalid operation payload') from None
            finally:
                request.state.remote_activity = export_activity(activity)
        if token is None:
            return result  # Authenticated legacy callers remain compatible during worker-first rollout.
        result = {**jsonable_encoder(result), '_activity': request.state.remote_activity}
        encoded = json.dumps(result, separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_AGENT_BYTES:
            raise HTTPException(503, 'Agent response exceeds limit')
        return wrap_transport(encoded)

    return app
