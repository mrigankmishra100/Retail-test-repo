"""Bounded authenticated workflow forwarding; no retries or local fallback."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from time import monotonic
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from uuid import uuid4
import re

from app.agents.remote_contract import (AgentInvocation, validate_agent_endpoint,
    validate_service_key, key_probe, MAX_AGENT_BYTES, MAX_REQUEST_BYTES,
    wrap_transport, transport_signature, transport_payload, decode_transport, MAX_RESPONSE_WIRE_BYTES)
from app.agents.contracts import RetailWorkflowResponse, SupplierWorkflowResponse
from app.contracts import ChatResponse
from app.enterprise_ai.responses_transport import NoRedirect
from app.observability import LangfuseObservabilityClient, TraceContext
from app.observability.context import request_trace_id
from app.services.cases import CaseNotFound, CaseConflict

logger = logging.getLogger(__name__)


def data(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


class RemoteAgentClient:
    def __init__(self, *, audience, endpoint, key, timeout=250, endpoint_resolver=None,
                 observability=None):
        if audience not in {'retail', 'supplier'}:
            raise ValueError('Invalid agent audience')
        self.audience, self.endpoint = audience, validate_agent_endpoint(endpoint)
        validate_service_key(key)
        self._key, self.timeout = key, timeout
        self._endpoint_resolver = endpoint_resolver
        self.observability = observability or LangfuseObservabilityClient()

    def invoke(self, operation, payload, session):
        context = TraceContext(
            trace_id=request_trace_id.get(),
            session_id=str(session.session_id),
            role=session.role,
            supplier_id=session.supplier_id,
            agent=self.audience,
        )
        with self.observability.observation(
            f"agent.remote.{self.audience}.{operation}",
            input={"operation": operation, "payload": payload},
            metadata={"workflow": "remote-agent", "agent": self.audience},
            context=context,
        ) as observation:
            result = self._invoke(operation, payload, session)
            observation.update(
                output={"status": "success", "operation": operation},
                metadata={"status": "success"},
            )
            return result

    def _invoke(self, operation, payload, session):
        envelope = AgentInvocation(audience=self.audience, operation=operation,
            issued_at=int(datetime.now(timezone.utc).timestamp()), nonce=uuid4(), session=session, payload=payload)
        envelope.check_identity(self.audience)
        body = envelope.model_dump_json().encode()
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError('Agent request exceeds size limit')
        wrapper = wrap_transport(body)
        wire = json.dumps(wrapper, separators=(',', ':')).encode('ascii')
        probe_nonce = str(uuid4())
        endpoint = validate_agent_endpoint(self._endpoint_resolver() if self._endpoint_resolver else self.endpoint)
        request = Request(endpoint + '/internal/agent/invoke', method='POST', data=wire,
            headers={'Content-Type': 'application/json', 'X-Agent-Signature': transport_signature(wrapper['payload'], self._key),
                     'X-Agent-Probe-Nonce': probe_nonce,
                     'X-Agent-Probe-Proof': key_probe(probe_nonce, self._key)})
        # Destination is validated configuration/registry data; never log the key, MAC or payload.
        logger.info('Agent outbound agent=%s operation=%s target_application=%s signature_present=true signature_length=64 signature_format_valid=true',
                    self.audience, operation, endpoint.split('/')[-3])
        logger.info('Agent transport agent=%s direction=outbound protocol=agent-base64-v1 payload_bytes=%d payload_sha256=%s key_probe=sent',
                    self.audience, len(body), sha256(body).hexdigest())
        started, status, remote_trace = monotonic(), None, None
        stage = 'transport'
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                status = response.status
                remote_trace = response.headers.get('X-Trace-ID')
                raw = response.read(MAX_RESPONSE_WIRE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_WIRE_BYTES:
                raise RuntimeError('Agent response exceeds limit')
            stage = 'response_validation'
            # Responses use the same opaque encoding so required null fields survive the gateway.
            result = json.loads(decode_transport(transport_payload(raw), MAX_AGENT_BYTES))
            model = ChatResponse if operation == 'chat' else (
                RetailWorkflowResponse if self.audience == 'retail' else SupplierWorkflowResponse)
            validated = model.model_validate(result)
            stage = 'completed'
            return validated.model_dump(mode='json') if operation == 'chat' else validated
        except HTTPError as exc:
            stage = 'upstream_http_error'
            status = exc.code
            remote_trace = exc.headers.get('X-Trace-ID')
            exc.close()
            # Do not relay remote exception text, bodies, URLs, headers or credentials.
            if status == 404:
                raise CaseNotFound('Workflow not found or expired') from None
            if status == 409:
                raise CaseConflict('Workflow conflict; refresh and review before retrying') from None
            if status == 403:
                raise PermissionError('Agent denied the requested action') from None
            if status == 422:
                raise ValueError('Agent rejected the workflow request') from None
            raise RuntimeError('Agent service unavailable; do not automatically replay the action') from None
        except Exception:
            raise RuntimeError('Agent service unavailable; do not automatically replay the action') from None
        finally:
            safe_trace = remote_trace if isinstance(remote_trace, str) and re.fullmatch(r'[a-fA-F0-9-]{36}', remote_trace) else None
            logger.info('Agent request agent=%s operation=%s http_status=%s agent_trace_id=%s elapsed_ms=%d stage=%s',
                        self.audience, operation, status, safe_trace, int((monotonic() - started) * 1000), stage)


class RemoteWorkflowService:
    def __init__(self, client):
        self.client = client

    def start(self, request, *, session, key):
        return self.client.invoke('start', {'request': data(request), 'key': key}, session)

    def get(self, thread_id, *, session):
        return self.client.invoke('get', {'thread_id': str(thread_id)}, session)

    def select(self, thread_id, selection, *, session):
        return self.client.invoke('select', {'thread_id': str(thread_id), 'selection': data(selection)}, session)

    def resume(self, thread_id, *, session):
        return self.client.invoke('resume', {'thread_id': str(thread_id)}, session)


class RemoteConversationService:
    def __init__(self, client):
        self.client = client

    def respond(self, request, *, session):
        return self.client.invoke('chat', {'request': data(request)}, session)
