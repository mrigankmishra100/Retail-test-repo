"""External providers execute the same graph, with session isolation and no baseline fallback."""
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.chat_api import router
from app.chat_telemetry import ChatTrace
from app.config import Settings
from app.contracts import ChatRequest
from app.dependencies import ApplicationContainer
from app.enterprise_ai.external import ExternalModelClient, ExternalModelUnavailable
from app.enterprise_ai.responses import EnterpriseAIResponsesClient
from app.external_models import ExternalModelInput, ExternalModelStore
from app.http_contract import install_http_contract
from app.services.cases import CaseConflict
from app.services.sessions import SessionContext
from app.services.retail_workflow import RetailWorkflowService
from app.session_api import retail_session

KEY = 'fixture-secret-external-key'


def actor():
    return SessionContext(session_id=uuid4(), role='retail-manager', expires_at=datetime.now(timezone.utc)+timedelta(hours=1))


def config(provider='gemini', **changes):
    return ExternalModelInput(**{'provider': provider, 'model_id': 'test-model', 'api_key': KEY, **changes})


def provider_result(provider, *, call=False, text='Review the inventory.', signature='signature'):
    if provider == 'openai':
        return {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'output': [
            {'type': 'function_call', 'call_id': 'c1', 'name': 'get_inventory_risk', 'arguments': '{}'} if call else
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}]}
    if provider == 'gemini':
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'functionCall': {'id': 'c1', 'name': 'get_inventory_risk', 'args': {}}, 'thoughtSignature': signature} if call else {'text': text}]}}],
            'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 20, 'thoughtsTokenCount': 5}}
    return {'stop_reason': 'tool_use' if call else 'end_turn', 'content': [
        {'type': 'tool_use', 'id': 'c1', 'name': 'get_inventory_risk', 'input': {}} if call else {'type': 'text', 'text': text}],
        'usage': {'input_tokens': 100, 'output_tokens': 20, 'cache_read_input_tokens': 10, 'cache_creation_input_tokens': 5}}


@pytest.fixture
def container(tmp_path):
    return ApplicationContainer(Settings(_env_file=None, enterprise_ai_model_id='baseline',
        chat_metrics_path=tmp_path/'metrics.sqlite3', chat_model_profiles=[{'id': 'baseline', 'label': 'OCI', 'model_id': 'baseline'}]))


@pytest.mark.parametrize('provider', ['openai', 'gemini', 'anthropic'])
def test_full_graph_function_round_trip_uses_selected_provider(container, provider):
    session = actor()
    connection = container.external_models.register(config(provider), session)
    responses = [provider_result(provider, text='IN_SCOPE'), provider_result(provider, call=True), provider_result(provider)]
    payloads = []
    def fake_post(client, path, payload):
        assert client.provider == provider
        payloads.append(payload)
        return responses.pop(0)
    with patch.object(ExternalModelClient, '_post', fake_post), patch('app.agents.tool_access.RetailToolAccess.call', return_value={'items': []}) as tool, patch.object(EnterpriseAIResponsesClient, 'create_response_sync', side_effect=AssertionError('Baseline must not run')):
        result = container.chat_experience_service.respond(ChatRequest(message='Which inventory is at risk?', model_profile=connection.profile.id), session=session, evaluation=True)
    tool.assert_called_once_with('get_inventory_risk', days=None)
    assert result['message'] == 'Review the inventory.'
    assert result['metrics']['call_count'] == 3
    assert result['metrics']['model_id'] == 'test-model'
    assert result['metrics']['provider'] == provider
    requests = [event for event in result['metrics']['events'] if event['stage'] == 'model_tool_request']
    assert len(requests) == 1 and requests[0]['tool'] == 'get_inventory_risk'
    assert requests[0]['api_source'] == ('openai_responses' if provider == 'openai' else provider)
    assert result['metrics']['input_tokens'] == (345 if provider == 'anthropic' else 300)
    assert result['metrics']['output_tokens'] == (75 if provider == 'gemini' else 60)
    assert result['metrics']['model_cost_usd'] is None
    assert KEY not in json.dumps(result)
    assert len(payloads) == 3
    assert not payloads[0].get('tools')
    second = json.dumps(payloads[2])
    assert 'items' in second
    if provider == 'gemini':
        assert 'thoughtSignature' in second and 'signature' in second and 'functionResponse' in second
    if provider == 'anthropic': assert 'tool_use_id' in second
    if provider == 'openai': assert 'function_call_output' in second


def test_session_catalog_key_redaction_expiry_and_model_identity(container):
    session, other = actor(), actor()
    connection = container.external_models.register(config(), session)
    catalog = container.chat_experience_service.catalog(session=session)
    assert KEY not in json.dumps(catalog)
    assert KEY not in repr(config())
    assert KEY not in json.dumps(config().model_dump(mode='json'))
    assert any(p['id'] == connection.profile.id for p in catalog['models'])
    assert all(p['id'] != connection.profile.id for p in container.chat_experience_service.catalog(session=other)['models'])
    with pytest.raises(ValueError):
        container.chat_experience_service.selection(ChatRequest(message='Inventory', model_profile=connection.profile.id), other)
    with pytest.raises(CaseConflict): container.external_models.client(connection.profile.id, other)
    container.external_models.connections[connection.profile.id].expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
    with pytest.raises(CaseConflict): container.external_models.client(connection.profile.id, session)
    assert not container.external_models.connections
    assert not ExternalModelStore().profiles(session)


def test_comparison_runs_two_external_profiles_with_read_only_tools(container):
    from app.services.chat_experience import EvaluationRequest
    session = actor()
    connections = [container.external_models.register(config(provider), session) for provider in ('gemini', 'anthropic')]
    request = EvaluationRequest(message='Which inventory is at risk?', model_profiles=[c.profile.id for c in connections], use_file_search=False)
    service = container.chat_experience_service
    job = service.reserve_evaluation(request, session=session)
    calls = []
    def respond(client, path, payload):
        calls.append(client.provider)
        return provider_result(client.provider, text='IN_SCOPE' if 'Classify whether' in json.dumps(payload) else 'Review the inventory.')
    with patch.object(ExternalModelClient, '_post', respond):
        service.evaluate(job['run_id'], request, session)
    result = service.get_evaluation(job['run_id'], session=session)
    assert result['status'] == 'completed' and calls == ['gemini', 'gemini', 'anthropic', 'anthropic']
    assert all(row['metrics']['status'] == 'completed' for row in result['results'])
    assert KEY not in json.dumps(result)
    with pytest.raises(ValueError):
        service.reserve_evaluation(request, session=actor())


def test_connection_api_requires_session_and_does_not_reflect_key(container):
    app = FastAPI(); app.include_router(router); install_http_contract(app)
    session = actor(); app.dependency_overrides[retail_session] = lambda: session
    with patch('app.chat_api.get_container', return_value=container), patch.object(ExternalModelClient, 'validate_connection'), TestClient(app) as client:
        response = client.post('/chat/external-models', json=config().wire())
        assert response.status_code == 201
        assert KEY not in response.text
        bad = client.post('/chat/external-models', json={**config().wire(), 'endpoint': 'http://localhost', 'extra_secret': KEY})
        assert bad.status_code == 422 and KEY not in bad.text and 'localhost' not in bad.text
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        assert client.post('/chat/external-models', json=config().wire()).status_code == 401


@pytest.mark.parametrize('provider,host', [('openai', 'api.openai.com'), ('gemini', 'generativelanguage.googleapis.com'), ('anthropic', 'api.anthropic.com')])
def test_fixed_host_headers_and_redirects_never_receive_secrets(provider, host):
    client, requests = ExternalModelClient(config(provider)), []
    def transport(request):
        requests.append(request)
        assert request.url.host == host
        assert KEY not in str(request.url)
        assert request.headers['authorization' if provider == 'openai' else 'x-goog-api-key' if provider == 'gemini' else 'x-api-key'].endswith(KEY)
        return httpx.Response(302, headers={'Location': 'http://localhost/steal'})
    client.http_transport = httpx.MockTransport(transport)
    with pytest.raises(ExternalModelUnavailable): client._post('/messages', {})
    assert len(requests) == 1


def test_provider_errors_are_redacted_and_record_usage_before_token_limit():
    client = ExternalModelClient(config('anthropic'))
    client.http_transport = httpx.MockTransport(lambda request: httpx.Response(401, text=KEY))
    with pytest.raises(ExternalModelUnavailable) as exc: client._post('/messages', {})
    assert KEY not in str(exc.value) and exc.value.public_code == 'external_authentication_failed'
    raw = provider_result('anthropic'); raw['stop_reason'] = 'max_tokens'
    client._post = Mock(return_value=raw)
    with pytest.raises(ExternalModelUnavailable) as exc:
        client.create_response_sync([{'role': 'user', 'content': 'Question'}])
    assert exc.value.chat_result['usage']['input_tokens'] == 115
    assert exc.value.public_code == 'external_response_incomplete'


def test_bad_ids_arguments_and_empty_content_fail_closed():
    client = ExternalModelClient(config())
    for raw in [{'candidates': []}, {'candidates': [{'finishReason': 'STOP', 'content': {'parts': []}}]}]:
        client._post = Mock(return_value=raw)
        with pytest.raises(ExternalModelUnavailable): client.create_response_sync([{'role': 'user', 'content': 'Q'}])
    raw = provider_result('gemini', call=True)
    raw['candidates'][0]['content']['parts'] *= 2
    client._post = Mock(return_value=raw)
    with pytest.raises(ExternalModelUnavailable): client.create_response_sync([{'role': 'user', 'content': 'Q'}])
    with pytest.raises(ValueError): config(model_id='../../localhost')


def test_native_file_search_and_hosted_conversations_are_explicitly_unavailable():
    client = ExternalModelClient(config())
    for kwargs in [{'require_file_search': True}, {'conversation_id': 'oci-hosted'}]:
        with pytest.raises(ValueError): client.create_response_sync([{'role': 'user', 'content': 'Q'}], **kwargs)


def test_workflow_restores_external_selection_for_explanation_and_drafts(container, monkeypatch):
    session = actor(); connection = container.external_models.register(config('gemini'), session)
    calls, snapshots = [], {}
    narrative = json.dumps({'request_summary': 'Please review the replenishment request.', 'response_summary': 'Please review the supplier response.'})
    def reply(client, path, payload):
        calls.append(client.provider)
        return provider_result(client.provider, text=narrative)
    monkeypatch.setattr(ExternalModelClient, '_post', reply)
    class Graph:
        def __init__(self, **deps): self.deps = deps
        def invoke(self, command, cfg, **kwargs):
            self.deps['conversation'].container.responses.create_response_sync([{'role':'user', 'content':'Explain'}])
            self.deps['cases'].email_drafts.narratives.generate('Supplier')
            snapshots.setdefault(cfg['configurable']['thread_id'], {'status':'awaiting_selection', 'message':'Review'})
        def get_state(self, cfg):
            return SimpleNamespace(values=snapshots.get(cfg['configurable']['thread_id'], {}), tasks=[], next=('next',))
    monkeypatch.setattr('app.services.retail_workflow.build_retail_graph', lambda **deps: Graph(**deps))
    with patch.object(EnterpriseAIResponsesClient, 'create_response_sync', side_effect=AssertionError('Baseline must not run')):
        first = RetailWorkflowService(container).start({'model_profile':connection.profile.id}, session=session, key='first')
        original = json.dumps(snapshots)
        other = container.external_models.register(config('anthropic'), session)
        RetailWorkflowService(container).change_model(str(first.thread_id), other.profile.id, session=session)
        assert json.dumps(snapshots) == original
        RetailWorkflowService(container).resume(str(first.thread_id), session=session)
    assert calls == ['gemini', 'gemini', 'anthropic', 'anthropic']
    assert KEY not in json.dumps(container.cache.get_state('retail_owner:' + str(first.thread_id)))
    assert container.responses.model_id == 'baseline'
    container.external_models.connections.clear()
    with pytest.raises(CaseConflict): RetailWorkflowService(container).resume(str(first.thread_id), session=session)


def test_remote_connection_is_forwarded_only_through_signed_agent_contract(container):
    from app.agent_app import dispatch
    session = actor()
    envelope = SimpleNamespace(operation='connect_model', session=session, payload={'config':config().wire()})
    with patch.object(ExternalModelClient, 'validate_connection'):
        result = dispatch(container, 'retail', envelope)
    assert result['profile']['provider'] == 'gemini' and KEY not in json.dumps(result)
    with pytest.raises(Exception): dispatch(container, 'supplier', envelope)
    container.settings.agent_endpoints = {'retail':'configured'}
    container.remote_retail_agent = Mock()
    container.chat_experience_service.connect_external(config(), session=session)
    container.remote_retail_agent.invoke.assert_called_once_with('connect_model', {'config':config().wire()}, session)


def test_gemini_connection_checks_availability_and_rejects_404_without_registering(container):
    from app.external_models import ExternalModelDiscoveryInput
    session = actor()
    client = ExternalModelClient(config())
    requests = []
    def reply(request):
        requests.append(request)
        assert request.headers['x-goog-api-key'] == KEY and request.content == b''
        return httpx.Response(404, json={'error': {'message': KEY}})
    client.http_transport = httpx.MockTransport(reply)
    with pytest.raises(ExternalModelUnavailable) as exc: client.validate_connection()
    assert exc.value.public_code == 'external_model_not_found' and KEY not in str(exc.value)
    with patch.object(ExternalModelClient, 'validate_connection', side_effect=exc.value):
        with pytest.raises(ExternalModelUnavailable): container.chat_experience_service.connect_external(config(), session=session)
    assert not container.external_models.connections
    assert str(requests[0].url).endswith('/models/test-model')


def test_gemini_model_discovery_filters_text_methods_and_handles_pagination():
    client = ExternalModelClient(config())
    client._get = Mock(side_effect=[{'models': [
        {'name':'models/gemini-current-flash', 'supportedGenerationMethods':['generateContent']},
        {'name':'models/gemini-text-embedding', 'supportedGenerationMethods':['embedContent']},
        {'name':'models/gemini-image', 'supportedGenerationMethods':['generateContent']},
        {'name':'models/gemini-audio', 'supportedGenerationMethods':['generateContent']},
    ], 'nextPageToken':'page-two'}, {'models': [
        {'name':'models/gemini-another-flash', 'supportedGenerationMethods':['generateContent']},
        {'name':'models/gemini-current-flash', 'supportedGenerationMethods':['generateContent']},
    ]}])
    assert client.available_models() == ['gemini-another-flash','gemini-current-flash']
    assert client._get.call_args_list[1].args == ('/models', {'pageSize':1000,'pageToken':'page-two'})
    client._get = Mock(return_value={'name':'models/test-model','supportedGenerationMethods':['embedContent']})
    with pytest.raises(ExternalModelUnavailable) as exc: client.validate_connection()
    assert exc.value.public_code == 'external_model_method_unsupported'


@pytest.mark.parametrize('provider', ['gemini', 'openai', 'anthropic'])
def test_model_options_endpoint_requires_owner_and_discovery_does_not_persist_key(container, provider):
    session = actor(); app = FastAPI(); app.include_router(router); install_http_contract(app)
    app.dependency_overrides[retail_session] = lambda: session
    with patch('app.chat_api.get_container', return_value=container), patch.object(ExternalModelClient, 'available_models', return_value=['gemini-current-flash']), TestClient(app) as api:
        result = api.post('/chat/external-model-options', json={'provider':provider,'api_key':KEY})
        assert result.status_code == 200 and result.json() == {'models':['gemini-current-flash']}
        assert KEY not in result.text and not container.external_models.connections
    with patch('app.chat_api.get_container', return_value=container), patch.object(ExternalModelClient, 'validate_connection', side_effect=ExternalModelUnavailable('external_model_not_found', status=404)), TestClient(app) as api:
        result = api.post('/chat/external-models', json=config().wire())
        assert result.status_code == 503 and result.json()['error']['code'] == 'external_model_not_found'
        assert 'not available' in result.json()['error']['message'] and KEY not in result.text


def test_chat_returns_specific_safe_model_error_instead_of_generic_failure(container):
    from app.services.retail_conversation import RetailConversationService
    session = actor()
    service = RetailConversationService(container)
    container.responses.enabled = True
    service._load_history = Mock(return_value={'exists':False,'next_turn':1,'messages':[]})
    service._save_turn = Mock()
    service._run = Mock(side_effect=ExternalModelUnavailable('external_model_not_found', status=404))
    result = service.respond(ChatRequest(message='Which inventory needs review?'),session=session)
    assert result['llm_status'] == 'unavailable'
    assert 'load available models' in result['message'] and KEY not in json.dumps(result)
    assert service._save_turn.call_args.kwargs['error_code'] == 'external_model_not_found'


def test_workflow_metrics_distinguish_successful_graph_from_failed_ai(container):
    session = actor()
    profile = container.settings.chat_model_profiles[0]
    trace = ChatTrace(profile)
    graph = Mock(workflow_trace=trace, workflow_session=session)
    def invoke(*args, **kwargs): trace.record({'error_code':'external_model_not_found','http_status':404},100,error=True)
    graph.invoke.side_effect = invoke
    RetailWorkflowService(container)._run(graph, {}, {})
    metrics = container.chat_experience_service.metrics(session=session)['records'][0]['metrics']
    assert metrics['workflow_status'] == 'completed'
    assert metrics['status'] == 'ai_unavailable'


@pytest.mark.parametrize('provider,host,header', [
    ('openai', 'api.openai.com', 'authorization'),
    ('anthropic', 'api.anthropic.com', 'x-api-key'),
])
def test_provider_catalog_uses_authenticated_get_and_sorts_models(provider, host, header):
    client = ExternalModelClient(config(provider))
    requests = []
    def reply(request):
        requests.append(request)
        assert request.method == 'GET' and request.url.host == host
        assert request.url.path == '/v1/models' and request.content == b''
        assert request.headers[header] == ('Bearer ' + KEY if provider == 'openai' else KEY)
        assert KEY not in str(request.url)
        if provider == 'anthropic':
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert request.url.params['limit'] == '1000'
            if len(requests) == 1:
                return httpx.Response(200, json={'data': [{'id': 'z-model'}], 'has_more': True, 'last_id': 'z-model'})
            assert request.url.params['after_id'] == 'z-model'
        else:
            assert not request.url.query
        return httpx.Response(200, json={'data': [{'id': 'z-model'}, {'id': 'a-model'}, {'id': 'z-model'},
            {'id': 'bad/path'}, {'id': None}, {'id': 123}, None, {'id': 'x' * 151},
            {'id': 'retired-model', 'lifecycle': 'retired'}], 'has_more': False})
    client.http_transport = httpx.MockTransport(reply)
    assert client.available_models() == ['a-model', 'z-model']
    assert len(requests) == (2 if provider == 'anthropic' else 1)


def test_openai_catalog_and_connection_accept_fine_tuned_model_ids():
    identifier = 'ft:gpt-test:organization:project:abc123'
    client = ExternalModelClient(config('openai', model_id=identifier))
    client._get = Mock(return_value={'data': [{'id': identifier}]})
    assert client.available_models() == [identifier]


@pytest.mark.parametrize('payload', [
    {}, {'data': {}}, {'data': [], 'has_more': 'yes'},
    {'data': [], 'has_more': True}, {'data': [], 'has_more': True, 'last_id': ''},
    {'data': [], 'has_more': True, 'last_id': 123},
    {'data': [], 'has_more': True, 'last_id': 'x' * 4097},
])
def test_anthropic_catalog_rejects_malformed_pagination(payload):
    client = ExternalModelClient(config('anthropic'))
    client._get = Mock(return_value=payload)
    with pytest.raises(ExternalModelUnavailable) as exc:
        client.available_models()
    assert exc.value.public_code == 'external_response_invalid'


def test_anthropic_catalog_bounds_repeated_and_excessive_pages():
    client = ExternalModelClient(config('anthropic'))
    client._get = Mock(return_value={'data': [], 'has_more': True, 'last_id': 'same-cursor'})
    with pytest.raises(ExternalModelUnavailable) as exc: client.available_models()
    assert exc.value.public_code == 'external_response_invalid' and client._get.call_count == 2
    client._get = Mock(side_effect=[{'data': [], 'has_more': True, 'last_id': str(i)} for i in range(5)])
    with pytest.raises(ExternalModelUnavailable) as exc: client.available_models()
    assert exc.value.public_code == 'external_response_too_large' and client._get.call_count == 5


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
def test_provider_catalog_size_is_bounded(provider):
    client = ExternalModelClient(config(provider))
    client._get = Mock(return_value={'data': [{'id': f'model-{i}'} for i in range(1001)], 'has_more': False})
    with pytest.raises(ExternalModelUnavailable) as exc: client.available_models()
    assert exc.value.public_code == 'external_response_too_large'


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
@pytest.mark.parametrize('status,code', [(401, 'external_authentication_failed'), (429, 'external_rate_limit')])
def test_provider_catalog_errors_never_expose_provider_body_or_key(provider, status, code):
    client = ExternalModelClient(config(provider))
    client.http_transport = httpx.MockTransport(lambda request: httpx.Response(status, json={'error': KEY}))
    with pytest.raises(ExternalModelUnavailable) as exc: client.available_models()
    assert exc.value.public_code == code and KEY not in str(exc.value)
