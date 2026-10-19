"""Read-only REST delegation. URLs and credentials never enter model tool arguments."""
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import re
import socket
import ssl
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.chat_telemetry import event
from app.live_activity import activity_span
from app.services.cases import CaseService
from app.services.delivery_connections import DeliveryConnectionStore
from app.observability import LangfuseObservabilityClient, TraceContext
from app.observability.context import request_trace_id


def validate_delivery_url(value):
    """Public HTTPS DNS/IP endpoint, explicit ports supported; never URL credentials."""
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    port = parsed.port if parsed.port is not None else 443
    authority = ('[' + host + ']') if ':' in host else host
    if parsed.port is not None:
        authority += ':' + str(port)
    try:
        address = ipaddress.ip_address(host)
        valid_host = address.is_global and not address.is_multicast and str(address) == host
    except ValueError:
        valid_host = bool(re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host))
    if (len(value) > 2000 or parsed.scheme != 'https' or parsed.netloc != authority
            or not valid_host or not 1 <= port <= 65535
            or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid'))
            or not re.fullmatch(r'/[A-Za-z0-9/_-]+', parsed.path)
            or '?' in value or '#' in value or any(c.isspace() for c in value)):
        raise ValueError('Delivery endpoint must be public HTTPS with a valid port and path, without query or credentials')
    return parsed


def public_addresses(host, port=443):
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast for address in addresses):
        raise ValueError('Delivery endpoint did not resolve exclusively to public addresses')
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the validated IP with certificate verification for the original host."""
    def __init__(self, host, address, port=443):
        super().__init__(host, port=port, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class DistributorSource(BaseModel):
    model_config = ConfigDict(extra='ignore', hide_input_in_errors=True)
    source_id: str = Field(min_length=1, max_length=120)
    document_name: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(max_length=12000)
    page: int | None = Field(default=None, ge=1)
    score: float | None = Field(default=None, allow_inf_nan=False)


class DeliveryReply(BaseModel):
    """Distributor App /api/v1/answers response; request_id is generated remotely."""
    model_config = ConfigDict(extra='ignore', hide_input_in_errors=True)
    id: UUID
    document_id: UUID
    request_id: UUID
    question: str = Field(min_length=1, max_length=12000)
    answer: str = Field(min_length=1, max_length=16000)
    sources: list[DistributorSource] = Field(max_length=20)
    answer_status: Literal['answered', 'insufficient_information']
    demo_data: bool = Field(strict=True)
    model_id: str = Field(min_length=1, max_length=200)
    knowledge_version: str = Field(min_length=1, max_length=200)
    latency_ms: int = Field(ge=0)
    created_at: datetime


class DeliveryToolResult(BaseModel):
    status: Literal['completed', 'not_registered', 'not_connected', 'unavailable']
    message: str
    read_only: Literal[True] = True
    sources: list[DistributorSource] = Field(default_factory=list)
    demo_data: bool | None = None
    answer_status: Literal['answered', 'insufficient_information'] | None = None
    request_id: UUID | None = None
    model_id: str | None = None
    knowledge_version: str | None = None


class DeliveryHTTPError(Exception):
    def __init__(self, status):
        self.status = status


def post_delivery(endpoint, token, payload):
    parsed = validate_delivery_url(endpoint)
    port = parsed.port or 443
    addresses = public_addresses(parsed.hostname, port)
    # Pin the actual socket destination: no second DNS lookup, redirects or env proxies.
    connection = PinnedHTTPSConnection(parsed.hostname, addresses[0], port)
    deadline = monotonic() + 30
    try:
        headers = {'Content-Type': 'application/json',
            'Accept': 'application/json', 'Accept-Encoding': 'identity'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.request('POST', parsed.path, body=json.dumps(payload).encode('utf-8'), headers=headers)
        transport_socket = connection.sock
        transport_socket.settimeout(max(0.001, deadline - monotonic()))
        response = connection.getresponse()
        if response.status != 200:
            raise DeliveryHTTPError(response.status)
        if (response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json'
                or response.getheader('Content-Encoding', 'identity') != 'identity'):
            raise ValueError('Invalid delivery response type')
        chunks, size = [], 0
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError()
            # read1 performs at most one underlying read; reset the remaining total budget.
            transport_socket.settimeout(remaining)
            chunk = response.read1(min(8192, 65537 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 65536:
                raise ValueError('Delivery response too large')
            if response.isclosed():
                break
        raw_body = b''.join(chunks)
        reply = DeliveryReply.model_validate_json(raw_body)
        if reply.question != payload['question'] or not reply.answer.strip() or (token and token in raw_body.decode('utf-8')):
            raise ValueError('Invalid delivery response correlation')
        return reply
    finally:
        connection.close()


class DeliveryAgentService:
    def __init__(self, container):
        self.container = container
        self.store = DeliveryConnectionStore(container)
        self.tracing = getattr(container, 'langfuse', None) or LangfuseObservabilityClient()

    @staticmethod
    def _key(session):
        return f'delivery_connection_v2:{session.session_id}:{session.supplier_id}'

    @staticmethod
    def _context(session):
        return TraceContext(trace_id=request_trace_id.get(), session_id=str(session.session_id),
            supplier_id=session.supplier_id, role=session.role, agent='delivery')

    def connect(self, agent_id, token, *, session):
        CaseService._session(session).require_role('supplier')
        with self.tracing.observation('delivery.connect', metadata={'agent_id': str(agent_id)},
                context=self._context(session)) as observation:
            result = self._connect(agent_id, token, session=session)
            observation.update(output=result, metadata={'status': 'success'})
            return result

    def _connect(self, agent_id, token, *, session):
        agent = self.container.agent_registry_service._get(agent_id)
        if agent.agent_type not in {'CUSTOM', 'DELIVERY'}:
            raise ValueError('Select a Custom agent with the distributor question/answer API')
        validate_delivery_url(agent.endpoint_url)
        token = token.strip(' ')
        anonymous = not token
        if token.lower().startswith('bearer '):
            token = token[7:]
        if not anonymous and not re.fullmatch(r'[A-Za-z0-9._~+/-]{16,4096}={0,2}', token):
            raise ValueError('Invalid service token format')
        ttl = max(1, int((session.expires_at - datetime.now(timezone.utc)).total_seconds()))
        # Explicit session binding, not name guessing. Atomically replace the prior
        # destination and its token, and verify the same destination on every call.
        with self.store.workflow_lock(self._key(session)):
            if not self.store.set_state(self._key(session), {'token': token, 'auth_mode': 'none' if anonymous else 'bearer',
                    'agent_id': str(agent.agent_id), 'endpoint_url': agent.endpoint_url}, ttl_seconds=ttl):
                raise RuntimeError('Session cache unavailable')
            result = self.connection_status(session=session)
            if not result['connected'] or result['agent_id'] != str(agent.agent_id):
                raise RuntimeError('Connection could not be read back from session cache')
        return result

    def connection_status(self, *, session):
        """Session-only status; never serialize the cache entry or credential."""
        CaseService._session(session).require_role('supplier')
        saved = self.store.get_state(self._key(session))
        if not saved:
            return {'agent_id': None, 'connected': False, 'available': False,
                'state': 'not_connected', 'expires_at': None}
        agent_id = str(UUID(saved['agent_id']))
        from app.services.agent_registry import RegistryNotFound
        try:
            agent = self.container.agent_registry_service._get(agent_id)
        except RegistryNotFound:
            return {'agent_id': agent_id, 'connected': False, 'available': False,
                'state': 'registration_missing', 'expires_at': None}
        connected = bool(self._valid_auth(saved) and saved.get('endpoint_url') == agent.endpoint_url
            and agent.agent_type in {'CUSTOM', 'DELIVERY'})
        return {'agent_id': agent_id, 'connected': connected,
            'available': connected and agent.active,
            'state': 'ready' if connected and agent.active else 'inactive' if connected else 'reconnect_required',
            'expires_at': session.expires_at if connected else None}

    def disconnect(self, agent_id, *, session):
        CaseService._session(session).require_role('supplier')
        with self.tracing.observation('delivery.disconnect', metadata={'agent_id': str(agent_id)},
                context=self._context(session)) as observation:
            self.container.agent_registry_service._get(agent_id)
            with self.store.workflow_lock(self._key(session)):
                self.store.delete_state(self._key(session), agent_id=agent_id)
            result = {'connected': False}
            observation.update(output=result, metadata={'status': 'success'})
            return result

    def ask(self, message, *, session):
        CaseService._session(session).require_role('supplier')
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 12000:
            raise ValueError('Invalid delivery question')
        with self.tracing.observation('delivery.ask', context=self._context(session)) as observation:
            result = self._ask(message, session=session)
            observation.update(metadata={'status': result.status, 'answer_status': result.answer_status,
                'demo_data': result.demo_data, 'source_count': len(result.sources),
                'remote_request_id': str(result.request_id) if result.request_id else None},
                level='ERROR' if result.status == 'unavailable' else 'DEFAULT')
            return result

    def _ask(self, message, *, session):
        try:
            agent, saved, problem = self._resolve_connection(session)
            if problem is not None:
                event('delivery_agent', problem.status, tool='ask_delivery_agent')
                return problem
            # Their integration is a document Q&A API, not a supplier-identity API.
            # Identity still gates our tool/cache; only relevant question text leaves.
            payload = {'question': message}
            with activity_span('agent', 'Calling delivery agent via REST'), self.tracing.observation(
                    'delivery.http', metadata={'agent_id': str(agent.agent_id), 'transport': 'rest'},
                    context=self._context(session)) as observation:
                reply = post_delivery(agent.endpoint_url, saved['token'], payload)
                observation.update(metadata={'status': 'success', 'http_status': 200,
                    'model_id': reply.model_id, 'provider_latency_ms': reply.latency_ms,
                    'remote_request_id': str(reply.request_id), 'answer_status': reply.answer_status,
                    'source_count': len(reply.sources), 'demo_data': reply.demo_data})
            event('delivery_agent', 'completed', tool='ask_delivery_agent')
            return DeliveryToolResult(status='completed', message=reply.answer,
                sources=reply.sources, demo_data=reply.demo_data, answer_status=reply.answer_status,
                request_id=reply.request_id, model_id=reply.model_id, knowledge_version=reply.knowledge_version)
        except DeliveryHTTPError as exc:
            reason = ('Delivery agent requires valid authorization. Connect with a valid API token in Agent registry.'
                if exc.status in {401, 403} else 'Delivery agent is temporarily unavailable or rate limited. Please retry later.')
        except Exception:
            reason = 'Delivery agent could not be reached or returned an invalid response. Ask its owner to check the REST contract and service availability.'
        event('delivery_agent', 'failed', tool='ask_delivery_agent')
        return DeliveryToolResult(status='unavailable', message=reason)

    def _resolve_connection(self, session):
        """Check current registry/cache state; never contact the external API here."""
        saved = self.store.get_state(self._key(session))
        if not saved:
            return None, None, DeliveryToolResult(status='not_connected', message=
                'Delivery lookup is not available in this session. Register and activate a Custom distributor agent, then use Connect delivery API in Agent registry.')
        from app.services.agent_registry import RegistryNotFound
        try:
            agent = self.container.agent_registry_service._get(saved['agent_id'])
        except RegistryNotFound:
            return None, None, DeliveryToolResult(status='not_registered', message=
                'The connected delivery agent is no longer registered. Register and connect a distributor agent before asking for delivery options.')
        if agent.agent_type not in {'CUSTOM', 'DELIVERY'} or not agent.active:
            return None, None, DeliveryToolResult(status='not_registered', message=
                'The connected delivery agent is inactive or no longer eligible. Activate it or connect another Custom distributor agent in Agent registry.')
        if agent.endpoint_url != saved.get('endpoint_url') or not self._valid_auth(saved):
            return None, None, DeliveryToolResult(status='not_connected', message=
                'The delivery connection needs to be renewed. Reconnect the delivery API in Agent registry before calling it.')
        validate_delivery_url(agent.endpoint_url)
        return agent, saved, None

    @staticmethod
    def _valid_auth(saved):
        # Old token-bearing connections remain valid. A missing/corrupt credential
        # must never silently downgrade a bearer connection to anonymous access.
        mode = saved.get('auth_mode', 'bearer')
        return (mode == 'none' and not saved.get('token')) or (mode == 'bearer' and bool(saved.get('token')))

    def availability(self, *, session):
        """Credential-free capability snapshot; invocation checks again to avoid stale grants."""
        CaseService._session(session).require_role('supplier')
        try:
            _, _, problem = self._resolve_connection(session)
            return {'available': problem is None, 'message': problem.message if problem else ''}
        except Exception:
            return {'available': False, 'message': 'Delivery connection status is temporarily unavailable. Please retry after the registry and session cache are available.'}
