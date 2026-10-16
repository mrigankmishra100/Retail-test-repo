from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.case_api import invoke
from app.config import Settings
from app.dependencies import ApplicationContainer
from app.enterprise_ai.external import ExternalModelClient
from app.external_models import ExternalModelInput
from app.http_contract import install_http_contract
from app.observability.langfuse import LangfuseObservabilityClient, SafeObservation, sanitize_trace_data
from app.services.agent_registry import AgentRegistryService, AgentUnavailable


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('role', ['retail', 'supplier'])
def test_agent_inactive_message_survives_public_contract(wrapped, role):
    app = FastAPI()
    install_http_contract(app)
    def unavailable():
        raise AgentUnavailable(code=f'{role}_agent_inactive')
    @app.get('/example')
    def example():
        return invoke(unavailable) if wrapped else unavailable()
    response = TestClient(app).get('/example')
    assert response.status_code == 503
    assert response.json()['error']['code'] == f'{role}_agent_inactive'
    assert response.json()['error']['message'] == f'{role.title()} agent is deactivated. Activate it in Agent registry to continue.'
    assert 'idempotency' not in response.text


def test_disabled_agent_is_checked_before_missing_key():
    container = ApplicationContainer(Settings(_env_file=None))
    container.settings = SimpleNamespace(agent_registry_enforcement_enabled=True,
        agent_endpoints={'retail': 'unused'}, agent_service_keys={})
    container.agent_registry_service = Mock()
    container.agent_registry_service.require_enabled.side_effect = AgentUnavailable(code='retail_agent_inactive')
    with pytest.raises(AgentUnavailable) as error:
        container._remote_agent('retail')
    assert error.value.public_code == 'retail_agent_inactive'


def test_missing_worker_key_has_clean_configuration_error():
    container = ApplicationContainer(Settings(_env_file=None))
    container.settings = SimpleNamespace(agent_registry_enforcement_enabled=False,
        agent_endpoints={'retail': 'unused'}, agent_service_keys={})
    with pytest.raises(AgentUnavailable) as error:
        container._remote_agent('retail')
    assert error.value.public_code == 'retail_agent_not_configured'


def test_unregistered_remote_agent_blocked_but_local_mode_allowed():
    database = Mock()
    database.fetch_all.return_value = [{'registered_count': 0, 'active_count': 0}]
    registry = AgentRegistryService(database)
    registry.require_enabled('retail')
    with pytest.raises(AgentUnavailable) as error:
        registry.require_enabled('retail', require_registered=True)
    assert error.value.public_code == 'retail_agent_not_registered'


@pytest.mark.parametrize('provider', ['openai', 'gemini', 'anthropic'])
def test_external_generation_is_traced_without_changing_result(provider):
    client = ExternalModelClient(ExternalModelInput(provider=provider, model_id='example-model', api_key='synthetic-key'))
    captured = []
    observation = Mock()
    @contextmanager
    def observe(name, **kwargs):
        captured.append((name, kwargs))
        yield observation
    client.observability = SimpleNamespace(observation=observe)
    expected = {'status': 'completed', 'output': 'answer', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
    client._create_native_response = Mock(return_value=expected)
    client._create_responses_response = Mock(return_value=expected)
    assert client.create_response_sync([{'role': 'user', 'content': 'example'}]) is expected
    assert captured[0][0] == f'{provider}.generation'
    assert captured[0][1]['metadata']['provider'] == provider
    assert 'synthetic-key' not in str(captured)
    observation.update.assert_called_once()


def test_usage_filters_nested_provider_fields_and_sensitive_data():
    raw = Mock()
    SafeObservation(raw).update(output={'api_key': 'hidden'}, usage_details={
        'input_tokens': 10, 'output_tokens': 4, 'total_tokens': 14,
        'input_tokens_details': {'cached_tokens': 2}, 'secret': 'hidden'})
    assert raw.update.call_args.kwargs['usage_details'] == {'input': 10, 'output': 4, 'total': 14}
    assert 'hidden' not in str(raw.update.call_args)


def test_disabled_sdk_and_failed_exporter_do_not_block_work(monkeypatch):
    import sys
    factory = Mock(side_effect=RuntimeError('export unavailable'))
    monkeypatch.setitem(sys.modules, 'langfuse', SimpleNamespace(Langfuse=factory))
    with LangfuseObservabilityClient().observation('disabled'):
        pass
    factory.assert_not_called()
    tracing = LangfuseObservabilityClient(enabled=True, base_url='https://example.invalid', public_key='test', secret_key='test')
    with tracing.observation('enabled') as observation:
        observation.update(output='completed')
    factory.assert_called_once()
    with pytest.raises(ValueError, match='business error'):
        with tracing.observation('failure'):
            raise ValueError('business error')


def test_self_hosted_configuration_preserves_deployment_settings():
    path = Path(__file__).resolve().parents[1] / 'scripts/configure_langfuse.py'
    spec = importlib.util.spec_from_file_location('configure_langfuse_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    values = {'LANGFUSE_BASE_URL': 'https://tracing.example.invalid',
              'LANGFUSE_PUBLIC_KEY': 'test-public', 'LANGFUSE_SECRET_KEY': 'test-secret'}
    original = {'AGENT_SERVICE_KEYS': {'retail': 'original'}, 'langfuse_tracing_enabled': False}
    result = module.configure(original, values)
    assert result['LANGFUSE_TRACING_ENABLED'] is True
    assert result['AGENT_SERVICE_KEYS'] == original['AGENT_SERVICE_KEYS']
    assert 'langfuse_tracing_enabled' not in result
    assert original['langfuse_tracing_enabled'] is False
    for url in ['https://user:secret@example.invalid', 'https://example.invalid?key=secret', 'javascript:alert(1)']:
        with pytest.raises(ValueError):
            module.configure(original, {**values, 'LANGFUSE_BASE_URL': url})


def test_langfuse_settings_reject_incomplete_enabled_configuration():
    with pytest.raises(ValueError, match='configuration is incomplete'):
        Settings(_env_file=None, langfuse_tracing_enabled=True, langfuse_base_url=None,
                 langfuse_public_key=None, langfuse_secret_key=None)


def test_trace_status_does_not_claim_live_connectivity_or_expose_keys(monkeypatch):
    import app.chat_api as api
    tracing = LangfuseObservabilityClient(enabled=True, base_url='https://tracing.example.invalid',
        public_key='private-public-key', secret_key='private-secret')
    monkeypatch.setattr(api, 'get_container', lambda: SimpleNamespace(langfuse=tracing))
    result = api.tracing(session=object())
    assert result['status'] == 'configured'
    assert result['scope'] == 'This backend service'
    assert 'private' not in str(result)
    assert 'verify received traces' in result['message']


@pytest.mark.parametrize('code,expected', [('retail_agent_inactive', AgentUnavailable), ('untrusted_upstream_text', RuntimeError)])
def test_remote_availability_errors_relay_only_allowlisted_codes(monkeypatch, code, expected):
    import io
    import json
    from datetime import datetime, timedelta, timezone
    from urllib.error import HTTPError
    from uuid import uuid4
    from app.agents import remote
    from app.services.sessions import SessionContext
    endpoint = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.example/actions/invoke'
    failure = HTTPError(endpoint, 503, 'private provider detail', {}, io.BytesIO(json.dumps({'error': {'code': code, 'message': 'private detail'}}).encode()))
    monkeypatch.setattr(remote, 'build_opener', lambda *args: SimpleNamespace(open=Mock(side_effect=failure)))
    worker = remote.RemoteAgentClient(audience='retail', endpoint=endpoint, key='r' * 43)
    session = SessionContext(session_id=uuid4(), role='retail-manager', expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    with pytest.raises(expected) as error:
        worker.invoke('chat_catalog', {}, session)
    assert 'private' not in str(error.value)
    if expected is AgentUnavailable:
        assert error.value.public_code == code


def test_encrypted_provider_continuation_is_not_exported():
    result = sanitize_trace_data({'encrypted_content': 'opaque-private', 'thoughtSignature': 'provider-private'})
    assert 'opaque-private' not in str(result)
    assert 'provider-private' not in str(result)
