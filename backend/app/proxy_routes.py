"""Explicit route and query allowlist for the integrated browser API."""
import re
from urllib.parse import parse_qsl, urlencode
from fastapi import HTTPException

ID = r'[A-Za-z0-9_-]{1,64}'
ROUTES = (
    ('GET', r'health', 'status/health', set()),
    ('GET', r'ready', 'status/ready', set()),
    ('POST', r'sessions', None, set()),
    ('GET', r'sessions/me', None, set()),
    ('DELETE', r'sessions/me', None, set()),
    ('GET', r'registry/agents', None, {'limit', 'offset'}),
    ('POST', r'registry/agents', None, set()),
    ('POST', rf'registry/agents/{ID}/(activate|deactivate)', None, set()),
    ('POST', r'chat', None, set()),
    ('GET', r'inventory/risks', None, {'horizon_days'}),
    ('GET', r'cases', None, {'limit', 'offset'}),
    ('GET', rf'cases/{ID}', None, set()),
    ('GET', rf'cases/{ID}/supplier-email', None, set()),
    ('POST', rf'cases/{ID}/(manager-decision|supplier-decision|prepare|complete)', None, set()),
    ('POST', rf'cases/{ID}/dispatch/(request|response)', None, set()),
    ('GET', rf'items/{ID}/sales-history', None, {'days', 'limit', 'offset'}),
    ('GET', rf'items/{ID}/suppliers', None, {'quantity', 'expedited'}),
    ('GET', rf'supplier/items/{ID}/inventory', None, set()),
    ('GET', rf'supplier/items/{ID}/quote', None, {'quantity'}),
    ('GET', r'supplier/policy', None, set()),
    ('POST', r'(retail|supplier)/workflows', None, set()),
    ('GET', rf'(retail|supplier)/workflows/{ID}', None, set()),
    ('POST', rf'(retail|supplier)/workflows/{ID}/resume', None, set()),
    ('POST', rf'retail/workflows/{ID}/selection', None, set()),
    ('GET', r'memory/approved', None, {'item_id', 'limit', 'offset'}),
)


def integrated_target(method, path, query):
    for verb, pattern, target, permitted in ROUTES:
        if verb == method and re.fullmatch(pattern, path):
            if len(query) > 2048:
                raise HTTPException(400, 'Query too long')
            pairs = parse_qsl(query, keep_blank_values=True)
            if (len(pairs) != len({k for k, _ in pairs})
                    or any(k not in permitted or len(v) > 128 for k, v in pairs)):
                raise HTTPException(400, 'Unsupported query parameters')
            suffix = '?' + urlencode(pairs) if pairs else ''
            return (target or path) + suffix
    raise HTTPException(404, 'Unknown API route')


def session_headers(headers):
    """Forward only application bearer sessions and bounded idempotency keys."""
    result = {}
    authorization = headers.get('authorization')
    if authorization:
        if not re.fullmatch(r'Bearer [A-Za-z0-9_-]{43}', authorization):
            raise HTTPException(401, 'Invalid application session')
        result['Authorization'] = authorization
    key = headers.get('idempotency-key')
    if key:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key):
            raise HTTPException(400, 'Invalid idempotency key')
        result['Idempotency-Key'] = key
    return result
