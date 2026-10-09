"""Real Streamable HTTP MCP client with per-call, role-bound signed delegation.

No static end-user sessions, unsigned role claims, redirects, retries, or local
fallback. The MCP server still verifies persisted approval for mutation tools.
"""
import asyncio
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import logging
from time import monotonic
from uuid import uuid4
from app.utils.logger import provider_metadata
from app.utils.safe_diagnostics import protocol_details

logger = logging.getLogger(__name__)

from app.agents.remote_contract import (AgentInvocation, wrap_transport, decode_transport,
    validate_agent_endpoint, validate_service_key, MAX_REQUEST_BYTES)

delegated_session = ContextVar('remote_mcp_session', default=None)


def _mac(token, key):
    return hmac.new(validate_service_key(key).encode(),
                    b'retail-mcp-delegation-v1\x00' + token.encode('ascii'), sha256).hexdigest()


def assertion(name, arguments, session, key):
    role = 'retail' if session.role == 'retail-manager' else 'supplier'
    envelope = AgentInvocation(audience=role, operation='get', nonce=uuid4(),
        issued_at=int(datetime.now(timezone.utc).timestamp()), session=session,
        payload={'tool': name, 'arguments_sha256': arguments_digest(arguments)})
    envelope.check_identity(role)
    raw = envelope.model_dump_json().encode()
    if len(raw) > 6000:
        raise ValueError('MCP assertion exceeds header size limit')
    token = wrap_transport(raw)['payload']
    return token + '.' + _mac(token, key)


def arguments_digest(arguments):
    """Bind the complete normalized body without putting chat text in headers."""
    raw = json.dumps(arguments, sort_keys=True, separators=(',', ':'),
                     ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('MCP arguments exceed size limit')
    return sha256(raw).hexdigest()


def verify_assertion(value, keys, name, arguments, replay):
    try:
        if not isinstance(value, str) or len(value) > 8100:
            raise ValueError()
        token, supplied = value.split('.')
        # Verify MAC before decoding or acting on caller-controlled identity.
        matched = [role for role, key in keys.items() if hmac.compare_digest(_mac(token, key), supplied)]
        if len(matched) != 1:
            raise ValueError()
        envelope = AgentInvocation.model_validate_json(decode_transport(token, MAX_REQUEST_BYTES))
        envelope.check_identity(matched[0])
        # Accept old, fully signed bodies during an MCP-first rolling upgrade.
        expected = {'tool': name, 'arguments_sha256': arguments_digest(arguments)}
        legacy = {'tool': name, 'arguments': arguments}
        if envelope.operation != 'get' or envelope.payload not in (expected, legacy):
            raise ValueError()
        from app.agents.tool_access import RETAIL_TOOL_NAMES, SUPPLIER_TOOL_NAMES
        allowed = RETAIL_TOOL_NAMES if matched[0] == 'retail' else SUPPLIER_TOOL_NAMES
        if name not in allowed:
            raise ValueError()
        replay.claim(envelope.nonce)
        return envelope.session
    except Exception:
        raise PermissionError('Invalid, expired or replayed MCP delegation') from None


def call_remote(container, session, name, arguments):
    from app.mcp.tools import TOOL_FUNCTIONS
    from pydantic import TypeAdapter
    from inspect import signature
    from fastmcp.exceptions import ToolError
    class RemoteToolFailure(ToolError):
        """Only a fixed server boundary category, never an arbitrary SDK message."""
    started, stage, outcome = monotonic(), 'prepare', 'failed'
    call_id = str(uuid4())
    metadata = {'call_id':call_id, 'mcp_tool':name if name in TOOL_FUNCTIONS else '<unknown>'}

    async def run():
        nonlocal stage
        import httpx2
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        async def prefer_json(request):
            # OCI Hosted Applications converts responses to fragmented SSE when
            # Accept advertises SSE. These stateless tool calls require one JSON
            # response; override the SDK's per-request Accept at send time.
            if request.method == 'POST':
                request.headers['Accept'] = 'application/json'

        def factory(**kwargs):
            kwargs['follow_redirects'] = False
            kwargs['timeout'] = httpx2.Timeout(45, connect=10)
            kwargs['trust_env'] = False
            hooks = dict(kwargs.get('event_hooks') or {})
            hooks['request'] = [*hooks.get('request', []), prefer_json]
            kwargs['event_hooks'] = hooks
            return httpx2.AsyncClient(**kwargs)

        stage = 'connect'
        logger.info('mcp_remote_stage', extra={**metadata, 'stage': stage})
        transport = StreamableHttpTransport(endpoint, headers={'X-MCP-Assertion': header, 'X-MCP-Call-ID':call_id},
                                             httpx_client_factory=factory)
        async with Client(transport, timeout=45) as client:
            stage = 'schema_discovery'
            logger.info('mcp_remote_stage', extra={**metadata, 'stage': stage})
            await client.list_tools()
            stage = 'tool_execution'
            logger.info('mcp_remote_stage', extra={**metadata, 'stage': stage})
            result = await client.call_tool(name, arguments, timeout=45)
            if result.is_error:
                # Only the boundary's fixed category crosses back to the caller.
                # Never forward raw SDK/server text, tool arguments or secrets.
                codes = {'not_found', 'conflict', 'forbidden', 'unauthorized',
                         'validation_error', 'policy_unavailable', 'capability_unavailable'}
                code = 'dependency_unavailable'
                for part in result.content or ():
                    candidate = getattr(part, 'text', '').split(';', 1)[0]
                    if candidate in codes:
                        code = candidate
                        break
                raise RemoteToolFailure(code + '; remote MCP tool failed; no automatic replay')
            return result.structured_content

    try:
        settings = container.settings
        role = 'retail' if session.role == 'retail-manager' else 'supplier'
        metadata['agent'] = role
        arguments = TOOL_FUNCTIONS[name].input_model.model_validate(arguments).model_dump(mode='json')
        header = assertion(name, arguments, session, settings.remote_mcp_service_keys[role])
        endpoint = validate_agent_endpoint(settings.remote_mcp_endpoint) + '/mcp'
        logger.info('mcp_remote_started', extra=metadata)
        value = asyncio.run(run())
        stage = 'result_validation'
        if len(json.dumps(value)) > 2_000_000:
            raise ValueError('Oversized MCP result')
        model = TypeAdapter(signature(TOOL_FUNCTIONS[name]).return_annotation)
        result = model.dump_python(model.validate_python(value), mode='json')
        stage, outcome = 'completed', 'success'
        return result
    except Exception as exc:
        logger.warning('mcp_remote_failed', extra={**metadata,'stage':stage,
            'error_type':type(exc).__name__, **provider_metadata(exc), **protocol_details(exc)})
        if isinstance(exc, RemoteToolFailure):
            raise exc from None
        raise ToolError('dependency_unavailable; remote MCP call failed; no automatic replay') from None
    finally:
        logger.info('mcp_remote_completed', extra={**metadata,'stage':stage,'status':outcome,
            'duration_ms':round((monotonic()-started)*1000,2)})
