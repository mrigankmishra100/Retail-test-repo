"""Authenticated backend-to-agent contract, separate from public user inputs."""
from datetime import datetime, timezone
import base64
import binascii
import json
from hashlib import sha256
import hmac
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.services.sessions import SessionContext

MAX_AGENT_BYTES = 2_000_000
MAX_REQUEST_BYTES = 128_000
ASSERTION_LIFETIME = 30
TRANSPORT_VERSION = 'agent-base64-v1'
MAX_REQUEST_WIRE_BYTES = 4 * ((MAX_REQUEST_BYTES + 2) // 3) + 512
MAX_RESPONSE_WIRE_BYTES = 4 * ((MAX_AGENT_BYTES + 2) // 3) + 512


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def transport_payload(raw, *, allow_legacy=False):
    """Parse only a bounded wire wrapper, not its encoded business data."""
    try:
        wrapper = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError, UnicodeError):
        raise ValueError('Invalid agent transport') from None
    if allow_legacy and isinstance(wrapper, dict) and 'transport' not in wrapper:
        return None
    if (not isinstance(wrapper, dict) or set(wrapper) != {'transport', 'payload'}
            or wrapper['transport'] != TRANSPORT_VERSION or not isinstance(wrapper['payload'], str)
            or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', wrapper['payload'])):
        raise ValueError('Invalid agent transport')
    return wrapper['payload']


def wrap_transport(raw):
    token = base64.urlsafe_b64encode(raw).decode('ascii')
    return {'transport': TRANSPORT_VERSION, 'payload': token}


def transport_signature(token, key):
    # Domain separation prevents a v1 raw-body signature being reused as a v2 signature.
    return signature(b'agent-base64-v1\x00' + token.encode('ascii'), key)


def decode_transport(token, limit):
    if len(token) > 4 * ((limit + 2) // 3):
        raise ValueError('Agent payload exceeds limit')
    try:
        decoded = base64.b64decode(token, altchars=b'-_', validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Invalid agent encoding') from None
    if len(decoded) > limit or base64.urlsafe_b64encode(decoded).decode('ascii') != token:
        raise ValueError('Invalid agent encoding or size')
    return decoded


def validate_service_key(secret):
    key = secret.get_secret_value() if hasattr(secret, 'get_secret_value') else secret
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', key):
        raise ValueError('Agent service key requires 43-128 URL-safe characters; generate at least 32 random bytes')
    return key


def validate_agent_endpoint(value):
    parsed = urlsplit(value)
    # Deployment configuration only, never a URL supplied by an API caller/model.
    if (parsed.scheme != 'https' or parsed.query or parsed.fragment or parsed.username or parsed.password
            or not re.fullmatch(r'inference\.generativeai\.[a-z]+(?:-[a-z]+)+-\d+\.oci\.oraclecloud\.com', parsed.netloc)
            or not re.fullmatch(r'/\d{8}/hostedApplications/ocid1\.generativeaihostedapplication\.oc1\.[a-z0-9.-]+/actions/invoke/?', parsed.path)):
        raise ValueError('Agent endpoint must be an OCI Hosted Application HTTPS invoke base without a custom path')
    return value.rstrip('/')


def signature(body, key):
    return hmac.new(validate_service_key(key).encode(), body, sha256).hexdigest()


def key_probe(nonce, key):
    """Domain-separated proof for diagnostics only; never authorizes an operation."""
    return signature(b'retail-agent-key-diagnostic-v1\x00' + nonce.encode('ascii'), key)


def key_probe_outcome(headers, key):
    nonce = headers.get('x-agent-probe-nonce')
    proof = headers.get('x-agent-probe-proof')
    if nonce is None or proof is None:
        return 'missing'
    if (not re.fullmatch(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', nonce)
            or not re.fullmatch(r'[0-9a-f]{64}', proof)):
        return 'invalid_format'
    return 'verified' if hmac.compare_digest(proof, key_probe(nonce, key)) else 'mismatch'


class AgentInvocation(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    audience: Literal['retail', 'supplier']
    operation: Literal['start', 'get', 'select', 'resume', 'chat']
    issued_at: int
    nonce: UUID
    session: SessionContext
    payload: dict = Field(default_factory=dict)

    def check_identity(self, audience, now=None):
        now = now or datetime.now(timezone.utc)
        if self.audience != audience or not -5 <= now.timestamp() - self.issued_at <= ASSERTION_LIFETIME:
            raise PermissionError('Agent assertion is invalid or expired')
        if self.session.expires_at <= now:
            raise PermissionError('Session expired')
        self.session.require_role('retail-manager' if audience == 'retail' else 'supplier')
