"""Exercise the shipped Python frontend proxy, not Vite or mocked browser routes."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.frontend import FrontendSettings, create_frontend
from app.registry_api import delivery_service
from app.services.agent_registry import AgentRegistryService
from app.services.delivery_agent import DeliveryAgentService
from tests.test_agent_registry import api, auth, registration


@pytest.mark.parametrize('token', ['fixture-delivery-token-123456', ''])
def test_hosted_registry_connection_and_deletion_lifecycle(api, tmp_path, monkeypatch, token):
    backend, database, sessions = api
    container = SimpleNamespace(database=database, cache=sessions.cache,
        agent_registry_service=AgentRegistryService(database))
    backend.app.dependency_overrides[delivery_service] = lambda: DeliveryAgentService(container)
    (tmp_path / 'index.html').write_text('<html>fixture</html>', encoding='utf-8')
    frontend = create_frontend(FrontendSettings(backend_invoke_base='http://backend',
        backend_auth_mode='none', backend_api_contract='integrated',
        frontend_allow_anonymous=True, app_env='development', frontend_static_dir=str(tmp_path)))
    original_client = httpx.AsyncClient
    monkeypatch.setattr('app.frontend.httpx.AsyncClient', lambda **kwargs:
        original_client(transport=httpx.ASGITransport(app=backend.app), **kwargs))
    headers = auth(sessions, 'supplier')
    with TestClient(frontend) as client:
        status_path = '/api/registry/agents/delivery-connection'
        assert client.get(status_path).status_code == 401
        assert client.get(status_path, headers=auth(sessions)).status_code == 403
        created = client.post('/api/registry/agents', headers=headers,
            json=registration(agent_type='CUSTOM', active=True,
                endpoint_url='https://delivery.example.com/api/v1/answers'))
        assert created.status_code == 201
        path = '/api/registry/agents/' + created.json()['agent_id']
        saved = client.post(path + '/delivery-connection', headers=headers, json={'api_token': token})
        assert saved.status_code == 200 and saved.json()['available']
        status = client.get(status_path, headers=headers)
        assert status.json() == saved.json()
        assert status.headers['cache-control'] == 'no-store'
        assert 'fixture-delivery-token' not in status.text
        assert client.get(status_path, headers=auth(sessions, 'supplier')).json()['connected'] is False
        assert client.delete(path, headers=headers).status_code == 409  # Still active.
        assert client.delete(path + '/delivery-connection', headers=headers).json() == {'connected': False}
        assert client.get(status_path, headers=headers).json()['connected'] is False
        assert client.post(path + '/deactivate', headers=headers).status_code == 200
        assert client.delete(path, headers=headers).json()['deleted']
        assert client.get('/api/registry/agents', headers=headers).json()['items'] == []


def test_all_registry_api_routes_are_reachable_through_hosted_allowlist():
    from app.proxy_routes import integrated_target
    from app.registry_api import router
    for route in router.routes:
        path = route.path.lstrip('/').replace('{agent_id}', '11111111-1111-4111-8111-111111111111')
        for method in route.methods - {'HEAD', 'OPTIONS'}:
            assert integrated_target(method, path, '') == path


@pytest.mark.parametrize('method,path,query', [
    ('POST', 'registry/agents/delivery-connection', ''),
    ('DELETE', 'registry/agents/delivery-connection', ''),
    ('GET', 'registry/agents/11111111-1111-4111-8111-111111111111/delivery-connection', ''),
    ('DELETE', 'registry/agents/not-a-uuid', ''),
    ('GET', 'registry/agents/delivery-connection', 'token=fixture'),
    ('POST', 'registry/agents/11111111-1111-4111-8111-111111111111/delivery-connection/extra', ''),
])
def test_delivery_route_fix_does_not_open_arbitrary_proxy_paths(method, path, query):
    from fastapi import HTTPException
    from app.proxy_routes import integrated_target
    with pytest.raises(HTTPException) as rejected:
        integrated_target(method, path, query)
    assert rejected.value.status_code in {400, 404}
