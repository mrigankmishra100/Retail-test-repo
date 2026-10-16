"""Offline proof of deployment bootstrap and UI deactivation before outbound calls."""
from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.remote_contract import wrap_transport
from app.config import Settings
from app.dependencies import ApplicationContainer
from app.http_contract import install_http_contract
from app.oci.cache import OCICacheClient
from app.oci.database import OracleDatabaseClient, WRITE_OPERATIONS
from app.registry_api import router, registry_service
from app.session_api import get_session_service
from app.services.agent_registry import AgentRegistryService, AgentRegistration, AgentUnavailable
from app.services.sessions import SessionService
from tests.test_agent_registry import MemoryRegistryDatabase, REGISTERED, FALLBACK, db_error, registration


class BootstrapDatabase(MemoryRegistryDatabase):
    def close(self):
        pass

    def fetch_all(self, operation, parameters=None):
        if operation == 'registry_role_status':
            rows = [r for r in self.rows.values() if r['agent_type'] == parameters['agent_type']]
            return [{'registered_count': len(rows), 'active_count': sum(r['active'] for r in rows)}]
        return super().fetch_all(operation, parameters)

    def execute(self, operation, parameters):
        if operation == 'registry_bootstrap_agent':
            OracleDatabaseClient._resolve_operation(WRITE_OPERATIONS, operation, parameters)
            if any(r['agent_type'] == parameters['agent_type'] for r in self.rows.values()):
                return False
            operation = 'registry_create_agent'
        return super().execute(operation, parameters)


def test_bootstrap_is_idempotent_preserves_disabled_roles_and_old_urls():
    db = BootstrapDatabase()
    service = AgentRegistryService(db, enforcement_enabled=True)
    service.bootstrap({'retail': REGISTERED, 'supplier': FALLBACK})
    assert len(db.rows) == 2 and all(r['active'] for r in db.rows.values())
    retail = next(r for r in db.rows.values() if r['agent_type'] == 'RETAIL')
    service.set_active(retail['agent_id'], False)
    service.bootstrap({'retail': FALLBACK, 'supplier': REGISTERED})
    assert len(db.rows) == 2
    assert db.rows[retail['agent_id']]['active'] == 0
    assert db.rows[retail['agent_id']]['endpoint_url'] == REGISTERED
    with pytest.raises(AgentUnavailable, match='deactivated'):
        service.require_enabled('retail')
    service.require_enabled('supplier')


def test_bootstrap_preserves_manually_registered_inactive_entry():
    db = BootstrapDatabase()
    service = AgentRegistryService(db)
    existing = service.create(AgentRegistration(**registration()))
    service.bootstrap({'retail': FALLBACK})
    assert len(db.rows) == 1 and service._get(existing.agent_id) == existing


def test_all_urls_validated_before_any_bootstrap_write():
    db = Mock()
    service = AgentRegistryService(db)
    service.bootstrap({})
    db.execute.assert_not_called()
    with pytest.raises(ValueError):
        service.bootstrap({'retail': REGISTERED, 'supplier': 'http://localhost'})
    db.execute.assert_not_called()


def test_bootstrap_only_recovers_verified_concurrent_insert():
    db = Mock(execute=Mock(side_effect=db_error(1)))
    db.fetch_all.return_value = [{'registered_count': 1, 'active_count': 0}]
    AgentRegistryService(db).bootstrap({'retail': REGISTERED})
    db.fetch_all.return_value = [{'registered_count': 0, 'active_count': 0}]
    with pytest.raises(RuntimeError):
        AgentRegistryService(db).bootstrap({'retail': REGISTERED})
    db.execute.side_effect = db_error(1017)
    with pytest.raises(RuntimeError):
        AgentRegistryService(db).bootstrap({'retail': REGISTERED})


@pytest.mark.parametrize('audience,role', [('retail','retail-manager'), ('supplier','supplier')])
def test_ui_deactivation_blocks_signed_outbound_until_reactivated(monkeypatch, audience, role):
    from app.agents import remote
    container = ApplicationContainer(Settings(_env_file=None, agent_registry_enforcement_enabled=True,
        agent_endpoints={audience: FALLBACK}, agent_service_keys={audience: 'r' * 43}))
    db = BootstrapDatabase()
    container.database = db
    service = container.agent_registry_service
    service.bootstrap({audience: REGISTERED})
    sessions = SessionService(OCICacheClient(allow_memory=True), db)
    issued = sessions.create(role, 'SUP001' if role == 'supplier' else None)
    session = sessions.resolve(issued['session_token'])
    app = FastAPI()
    install_http_contract(app)
    app.include_router(router)
    app.dependency_overrides[registry_service] = lambda: service
    app.dependency_overrides[get_session_service] = lambda: sessions
    outbound = Mock(return_value=nullcontext(SimpleNamespace(status=200, headers={},
        read=lambda size: json.dumps(wrap_transport(b'{}')).encode())))
    monkeypatch.setattr(remote, 'build_opener', lambda *args: SimpleNamespace(open=outbound))
    worker = getattr(container, 'remote_' + audience + '_agent')
    headers = {'Authorization': 'Bearer ' + issued['session_token']}
    with TestClient(app) as browser:
        listed = browser.get('/registry/agents', headers=headers).json()
        assert listed['enforcement_enabled'] is True
        agent = listed['items'][0]
        def invoke():
            worker.invoke('get', {'thread_id': str(uuid4())}, session)
        # Synthetic response is intentionally invalid, but the signed request was sent.
        with pytest.raises(RuntimeError, match='Agent service unavailable'):
            invoke()
        assert outbound.call_count == 1
        assert outbound.call_args.args[0].full_url == REGISTERED + '/internal/agent/invoke'
        assert browser.post(f"/registry/agents/{agent['agent_id']}/deactivate", headers=headers).status_code == 200
        service.bootstrap({audience: FALLBACK})  # Simulated gateway restart.
        with pytest.raises(AgentUnavailable, match='deactivated'):
            invoke()
        assert outbound.call_count == 1  # No fallback destination or model call.
        assert browser.post(f"/registry/agents/{agent['agent_id']}/activate", headers=headers).status_code == 200
        with pytest.raises(RuntimeError, match='Agent service unavailable'):
            invoke()
        assert outbound.call_count == 2
    container.close()


def test_gateway_startup_runs_bootstrap_and_does_not_become_ready_on_failure(monkeypatch):
    from app import main
    container = Mock()
    container.settings = SimpleNamespace(agent_registry_bootstrap_enabled=True,
        agent_endpoints={'retail': REGISTERED})
    monkeypatch.setattr(main, 'get_container', lambda: container)
    monkeypatch.setattr(main.app.state, 'container', container)
    with TestClient(main.app):
        assert main.app.state.ready
        container.agent_registry_service.bootstrap.assert_called_once_with({'retail': REGISTERED})
    assert not main.app.state.ready
    container.agent_registry_service.bootstrap.side_effect = RuntimeError('database unavailable')
    with pytest.raises(RuntimeError, match='database unavailable'):
        with TestClient(main.app):
            pytest.fail('Startup must fail when deployment registration cannot be checked')
    assert not main.app.state.ready


def test_image_defaults_survive_current_recipe_json(monkeypatch):
    monkeypatch.setenv('AGENT_REGISTRY_ENFORCEMENT_ENABLED', 'true')
    monkeypatch.setenv('AGENT_REGISTRY_BOOTSTRAP_ENABLED', 'true')
    monkeypatch.setenv('APP_CONFIG_JSON', json.dumps({'APPLICATION_ROLE': 'backend',
        'ENTERPRISE_AI_AGENT_REGISTRY_ENABLED': False, 'ENTERPRISE_AI_MCP_REGISTRY_ENABLED': False,
        'AGENT_ENDPOINTS': {'retail': REGISTERED}, 'AGENT_SERVICE_KEYS': {'retail': 'r' * 43}}))
    settings = Settings(_env_file=None)
    assert settings.agent_registry_enforcement_enabled and settings.agent_registry_bootstrap_enabled
    assert not settings.enterprise_ai_agent_registry_enabled
