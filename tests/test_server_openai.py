"""Configured credentials resolve on the model host, never in the browser."""
import json
from unittest.mock import Mock, patch
import pytest
from pydantic import SecretStr, ValidationError
from app.enterprise_ai.external import ExternalModelClient, ExternalModelUnavailable
from app.external_models import ExternalModelInput, ExternalModelDiscoveryInput
from tests.test_external_models import actor, container, KEY, provider_result
from app.contracts import ChatRequest


def test_configured_openai_discovery_and_same_tool_workflow(container):
    container.settings.openai_api_key = SecretStr(KEY)
    session = actor()
    request = ExternalModelDiscoveryInput(provider='openai', use_server_key=True)
    def catalog(client):
        assert client.provider == 'openai' and client._key.get_secret_value() == KEY
        return ['fixture-text-model']
    with patch.object(ExternalModelClient, 'available_models', catalog):
        assert container.chat_experience_service.discover_external(request, session=session) == {'models':['fixture-text-model']}
    config = ExternalModelInput(provider='openai', use_server_key=True, model_id='fixture-text-model', max_tokens=None)
    connection = container.chat_experience_service.connect_external(config, session=session)
    assert KEY not in json.dumps(connection)
    assert KEY not in json.dumps(container.chat_experience_service.catalog(session=session))
    assert KEY not in repr(container.settings)
    outputs = [provider_result('openai', text='IN_SCOPE'), provider_result('openai', call=True), provider_result('openai')]
    with patch.object(ExternalModelClient, '_post', side_effect=outputs), patch('app.agents.tool_access.RetailToolAccess.call', return_value={'items':[]}) as tool:
        result = container.chat_experience_service.respond(ChatRequest(message='Which inventory is at risk?', model_profile=connection['profile']['id']), session=session, evaluation=True)
    assert result['metrics']['provider'] == 'openai'
    tool.assert_called_once_with('get_inventory_risk', days=None)
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize('limit', [None, 4096, 16000])
def test_openai_output_budget_is_optional_without_changing_explicit_limits(limit):
    config = ExternalModelInput(provider='openai', api_key=KEY, model_id='fixture-text-model', max_tokens=limit)
    client = ExternalModelClient(config)
    with patch.object(client, '_post', return_value=provider_result('openai')) as post:
        client.create_response_sync([{'role':'user', 'content':'Check inventory'}])
    payload = post.call_args.args[1]
    if limit is None:
        assert 'max_output_tokens' not in payload
    else:
        assert payload['max_output_tokens'] == limit


@pytest.mark.parametrize('provider', ['gemini', 'anthropic'])
def test_other_provider_output_defaults_are_preserved(provider):
    config = ExternalModelInput(provider=provider, api_key=KEY, model_id='test-model')
    assert config.max_tokens == 4096
    with pytest.raises(ValidationError):
        ExternalModelInput(provider=provider, api_key=KEY, model_id='test-model', max_tokens=None)


def test_gateway_forwards_only_server_selection_not_credential(container):
    container.settings.agent_endpoints = {'retail':'https://retail.example.com'}
    container.settings.openai_api_key = SecretStr(KEY)
    remote = Mock()
    with patch.object(type(container), 'remote_retail_agent', new_callable=lambda: property(lambda _: remote)):
        container.chat_experience_service.discover_external(ExternalModelDiscoveryInput(provider='openai', use_server_key=True), session=actor())
    args = remote.invoke.call_args.args
    assert args[0] == 'discover_models'
    assert args[1]['config']['use_server_key'] is True and args[1]['config']['api_key'] is None
    assert KEY not in repr(args)


def test_absent_server_key_has_actionable_safe_error(container):
    with pytest.raises(ExternalModelUnavailable) as error:
        container.chat_experience_service.discover_external(ExternalModelDiscoveryInput(provider='openai', use_server_key=True), session=actor())
    assert error.value.public_code == 'openai_not_configured'


def test_missing_shared_key_api_contract_allows_session_key_fallback(container):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.chat_api import router
    from app.http_contract import install_http_contract
    from app.session_api import retail_session

    container.settings.openai_api_key = None
    session = actor()
    app = FastAPI()
    app.include_router(router)
    install_http_contract(app)
    app.dependency_overrides[retail_session] = lambda: session
    with patch('app.chat_api.get_container', return_value=container), TestClient(app) as client:
        missing = client.post('/chat/external-model-options', json={'provider': 'openai', 'use_server_key': True})
        assert missing.status_code == 503
        assert missing.json()['error']['code'] == 'openai_not_configured'
        with patch.object(ExternalModelClient, 'available_models', return_value=['fixture-text-model']):
            catalog = client.post('/chat/external-model-options', json={'provider': 'openai', 'api_key': KEY})
        assert catalog.status_code == 200
        assert catalog.json() == {'models': ['fixture-text-model']}
        assert not container.external_models.connections
        with patch.object(ExternalModelClient, 'validate_connection'):
            connected = client.post('/chat/external-models', json={
                'provider': 'openai', 'api_key': KEY, 'model_id': 'fixture-text-model', 'max_tokens': None})
        assert connected.status_code == 201
        profile = connected.json()['profile']
        assert profile['provider'] == 'openai'
        assert KEY not in missing.text + catalog.text + connected.text
        assert container.settings.openai_api_key is None
        assert profile['id'] not in [row.id for row in container.external_models.profiles(actor())]


@pytest.mark.parametrize('values', [
    {'provider':'gemini', 'use_server_key':True},
    {'provider':'openai', 'use_server_key':True, 'api_key':KEY},
    {'provider':'openai'},
])
def test_server_key_cannot_change_provider_or_override_credentials(values):
    with pytest.raises(ValidationError):
        ExternalModelInput(model_id='test-model', **values)


def test_private_runtime_json_accepts_openai_without_new_required_settings(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv('APP_CONFIG_JSON', json.dumps({'OPENAI_API_KEY':KEY}))
    settings = Settings(_env_file=None)
    assert settings.openai_api_key.get_secret_value() == KEY
    assert KEY not in repr(settings) + settings.model_dump_json()


def test_key_helper_preserves_hosted_config_and_never_overwrites(tmp_path, monkeypatch, capsys):
    from scripts.configure_openai import main
    source, key, target = (tmp_path / name for name in ['retail.json', 'key.txt', 'new.json'])
    source.write_text(json.dumps({'APPLICATION_ROLE':'retail-agent', 'UNCHANGED':'value'}))
    key.write_text('API key: sk-fixture-private-key')
    monkeypatch.setattr('sys.argv', ['configure_openai', '--config', str(source), '--key-file', str(key), '--output', str(target)])
    main()
    assert json.loads(target.read_text()) == {'APPLICATION_ROLE':'retail-agent', 'UNCHANGED':'value', 'OPENAI_API_KEY':'sk-fixture-private-key'}
    assert 'sk-fixture' not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        main()
    assert 'OPENAI_API_KEY' not in json.loads(source.read_text())


@pytest.mark.parametrize('config', [{}, {'APPLICATION_ROLE':'supplier-agent'}, {'APPLICATION_ROLE':'backend'}])
def test_key_helper_rejects_wrong_hosted_service(config):
    from scripts.configure_openai import configure
    with pytest.raises(ValueError):
        configure(config, 'sk-fixture-private-key')
