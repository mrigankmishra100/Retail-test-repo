"""Offline checks inside each release image; never invokes a cloud service."""
import hashlib
import os
import sys
from pathlib import Path

# Match `python -m uvicorn` from WORKDIR /app, not the installed base package.
sys.path.insert(0, '/app')

role = os.environ['VERIFY_ROLE']
expected_tag = os.environ['VERIFY_TAG']
assert os.environ.get('APP_RELEASE_TAG') == expected_tag
source = Path('/release-source/backend/app')
packaged = Path('/app/app')
files = sorted(source.rglob('*.py'))
assert len(files) >= 83
for file in files:
    actual = packaged / file.relative_to(source)
    assert actual.exists(), str(actual)
    assert hashlib.sha256(actual.read_bytes()).digest() == hashlib.sha256(file.read_bytes()).digest(), str(actual)
for forbidden in (Path('/app/.env'), Path('/app/runtime-config-private'), Path('/app/.oci')):
    assert not forbidden.exists(), str(forbidden)

from fastapi.testclient import TestClient
if role == 'frontend':
    from app.frontend import app
    with TestClient(app) as client:
        response = client.get('/')
        assert response.status_code == 200, response.status_code
    assets = list(Path('/app/static/assets').glob('*.js'))
    bundle = '\n'.join(p.read_text() for p in assets)
    assert 'Agent registry' in bundle
    assert 'Verified data answer' in bundle
    assert 'Replenishment' in bundle
else:
    from app.mcp.tools import TOOL_FUNCTIONS
    assert {'get_chat_history', 'save_chat_turn', 'query_retail_data', 'search_policy_documents',
            'send_supplier_request', 'send_supplier_response', 'get_workflow_case'} <= set(TOOL_FUNCTIONS)
    if role == 'backend':
        from app.main import app
        with TestClient(app) as client:
            assert client.get('/health').status_code == 200
            assert client.get('/status/health').json()['status'] == 'ok'
            assert client.get('/registry/agents').status_code == 401
    elif role in {'retail-agent', 'supplier-agent'}:
        from app.agent_main import app
        with TestClient(app) as client:
            assert client.get('/health').status_code == 200
            # No database access, model call or write can occur without authentication.
            assert client.post('/internal/agent/invoke', json={}).status_code in {401, 403}
    elif role == 'mcp-server':
        from app.mcp.server import app
        with TestClient(app) as client:
            assert client.get('/health').status_code == 200
    else:
        raise AssertionError('Unknown verification role')
print(f'PASS: {role}; {len(files)} source files match; local health/auth checks; release={expected_tag}')
