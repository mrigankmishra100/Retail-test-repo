import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import patch
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.chat_models import ChatModelProfile
from app.chat_telemetry import ChatTrace
from app.contracts import ChatRequest
from app.dependencies import ApplicationContainer
from app.regional_evaluation import RegionalEvaluationModel, REGIONAL_PROFILE
from app.services.chat_experience import EvaluationRequest
from app.services.sessions import SessionContext

SECRET = 'sk-fixture-regional-key-not-real'

def configuration(**updates):
    return dict(label='Chicago test', region='us-chicago-1', model_id='google.gemini-2.5-pro',
                project_id='ocid1.generativeaiproject.oc1.us-chicago-1.example', api_key=SECRET,
                vector_store_ids=['vs_chicago'], **updates)

@pytest.fixture
def context(tmp_path):
    settings = Settings(_env_file=None, chat_metrics_path=str(tmp_path / 'metrics.sqlite3'),
                        chat_model_profiles=[ChatModelProfile(id='a', label='A', model_id='baseline')])
    settings.enterprise_ai_responses_enabled = True
    settings.enterprise_ai_endpoint = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1'
    settings.enterprise_ai_project_id = 'ocid1.generativeaiproject.oc1.ap-hyderabad-1.example'
    settings.enterprise_ai_vector_store_ids = ['vs_hyderabad']
    settings.enterprise_ai_model_id = 'baseline'
    container = ApplicationContainer(settings)
    actor = SessionContext(session_id=uuid4(), role='retail-manager', expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    return container, actor

@pytest.mark.parametrize('field,value', [
    ('region', 'localhost:8000'), ('region', 'us-chicago-1.evil.com'),
    ('project_id', 'ocid1.generativeaiproject.oc1.ap-hyderabad-1.example'),
    ('api_key', 'invalid'), ('vector_store_ids', ['../other-project']),
    ('model_id', 'ocid1.generativeaimodel.oc1.us-chicago-1.example')])
def test_invalid_region_resources_and_credentials(field, value):
    data = configuration(); data[field] = value
    with pytest.raises(ValidationError):
        RegionalEvaluationModel(**data)

def test_no_credential_serialization():
    model = RegionalEvaluationModel(**configuration())
    request = EvaluationRequest(message='Explain stock coverage', model_profiles=['a', REGIONAL_PROFILE], regional_model=model)
    assert SECRET not in repr(model) + model.model_dump_json() + request.model_dump_json()
    assert 'regional_model' not in request.model_dump()
    with pytest.raises(ValidationError):
        EvaluationRequest(message='Explain', model_profiles=['a', REGIONAL_PROFILE])

def test_fresh_regional_transport_and_baseline_unchanged(context):
    container, actor = context
    old_transport = container.responses.transport
    model = RegionalEvaluationModel(**configuration())
    service = container.chat_experience_service
    scoped = service.scoped_container(model.profile(), True, ChatTrace(model.profile()), actor, regional=model)
    client = scoped.responses.client
    assert client.transport is not old_transport
    assert client.transport.base_url == model.endpoint
    assert client.transport.project_id == model.project_id
    assert client.transport.responses_auth_mode == 'genai_api_key'
    assert client.transport._genai_api_key.get_secret_value() == SECRET
    assert client.vector_store_ids == ['vs_chicago']
    assert container.responses.vector_store_ids != ['vs_chicago']
    assert container.responses.model_id == 'baseline'
    assert container.responses.transport is old_transport
    assert scoped.case_service.email_drafts.narratives.responses is scoped.responses
    no_rag = service.scoped_container(model.profile(), False, ChatTrace(model.profile()), actor, regional=model)
    assert no_rag.responses.vector_store_ids == []
    with pytest.raises(ValueError):
        service.respond(ChatRequest(message='Explain', model_profile=REGIONAL_PROFILE), session=actor, regional=model)

def test_evaluation_runs_same_workflow_and_persists_only_public_metadata(context):
    container, actor = context
    service = container.chat_experience_service
    model = RegionalEvaluationModel(**configuration())
    request = EvaluationRequest(message='Explain stock coverage', model_profiles=['a', REGIONAL_PROFILE], regional_model=model)
    run = service.reserve_evaluation(request, session=actor)
    models = []
    def evaluate(workflow, request, session):
        client = workflow.container.responses
        models.append(client.model_id)
        return {'message': 'Coverage is stock divided by daily demand.', 'llm_status': 'completed', 'used_tools': []}
    with patch.object(service, '_evaluate_one', side_effect=evaluate):
        service.evaluate(run['run_id'], request, actor)
    result = service.get_evaluation(run['run_id'], session=actor)
    assert models == ['baseline', model.model_id]
    assert len(result['results']) == 2
    assert result['results'][1]['metrics']['region'] == 'us-chicago-1'
    serialized = json.dumps(service.store.list(actor.session_id))
    assert SECRET not in serialized and model.project_id not in serialized
    assert result['regional_model']['region'] == 'us-chicago-1'
    assert service.catalog(session=actor)['models'][0]['id'] == 'a'
    assert all(item['id'] != REGIONAL_PROFILE for item in service.catalog(session=actor)['models'])

def test_regional_rag_does_not_borrow_default_store(context):
    container, actor = context
    data = configuration(); data['vector_store_ids'] = []
    request = EvaluationRequest(message='Read policies', model_profiles=['a', REGIONAL_PROFILE],
                                regional_model=RegionalEvaluationModel(**data), use_file_search=True)
    with pytest.raises(ValueError):
        container.chat_experience_service.reserve_evaluation(request, session=actor)


def native_configuration(**updates):
    return RegionalEvaluationModel(**(dict(label='Native Chicago', region='us-chicago-1',
        api_mode='native_chat', model_id='ocid1.generativeaimodel.oc1..example',
        compartment_id='ocid1.compartment.oc1..example') | updates))


@pytest.mark.parametrize('updates', [
    {'compartment_id': 'not-an-ocid'}, {'model_id': 'google.gemini-2.5-pro'},
    {'model_id': 'ocid1.generativeaimodel.oc1.ap-hyderabad-1.example'},
    {'vector_store_ids': ['vs_chicago']}, {'api_key': SECRET},
    {'project_id': 'ocid1.generativeaiproject.oc1.us-chicago-1.example'},
    {'max_completion_tokens': 999999}, {'config_profile': 'OTHER'},
])
def test_native_configuration_boundaries(updates):
    with pytest.raises(ValidationError):
        native_configuration(**updates)


def test_native_scoping_and_rag_rejection(context):
    from app.enterprise_ai.native_evaluation import NativeChatEvaluationClient
    container, actor = context
    regional = native_configuration()
    service = container.chat_experience_service
    scoped = service.scoped_container(regional.profile(), False, ChatTrace(regional.profile()), actor, regional)
    assert isinstance(scoped.responses.client, NativeChatEvaluationClient)
    assert scoped.responses.config_profile == 'DEFAULT'
    assert scoped.responses.config_file == container.settings.oci_config_file
    assert scoped.responses.endpoint == 'https://inference.generativeai.us-chicago-1.oci.oraclecloud.com'
    assert not scoped.settings.enterprise_ai_file_search_enabled
    assert not scoped.settings.enterprise_ai_conversation_state_enabled
    assert container.settings.enterprise_ai_model_id == 'baseline'
    request = EvaluationRequest(message='Read policies', model_profiles=['a', REGIONAL_PROFILE],
                                regional_model=regional, use_file_search=True)
    with pytest.raises(ValueError):
        service.reserve_evaluation(request, session=actor)


def test_native_sdk_tool_round_trip_through_existing_graph(context):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.agents.retail_conversation import run_conversation
    from app.enterprise_ai.native_evaluation import NativeChatEvaluationClient
    import oci
    m = oci.generative_ai_inference.models
    container, actor = context
    client = NativeChatEvaluationClient(native_configuration(), config_file='server-config')
    sdk = Mock()
    sdk.chat.side_effect = [
        SimpleNamespace(data=m.ChatResult(chat_response=m.GenericChatResponse(choices=[m.ChatChoice(
            finish_reason='tool_calls', message=m.AssistantMessage(tool_calls=[m.FunctionCall(
                id='call1', name='get_inventory_risk', arguments='{}')]))],
            usage=m.Usage(prompt_tokens=100, completion_tokens=20)))),
        SimpleNamespace(data=m.ChatResult(chat_response=m.GenericChatResponse(choices=[m.ChatChoice(
            finish_reason='stop', message=m.AssistantMessage(content=[m.TextContent(text='No stock risk found.')]),
            usage=m.Usage(prompt_tokens=120, completion_tokens=12))])))
    ]
    toolset = SimpleNamespace(container=container, session=actor, call=Mock(return_value={'items': []}))
    with patch('oci.config.from_file', return_value={}) as config, patch(
            'oci.generative_ai_inference.GenerativeAiInferenceClient', return_value=sdk) as constructor:
        result = run_conversation(responses=client, tools=toolset, message='Check stock risk')
    assert result['llm_status'] == 'completed'
    assert result['used_tools'] == ['get_inventory_risk']
    assert result['answer'] == 'No stock risk found.'
    toolset.call.assert_called_once_with('get_inventory_risk', days=None)
    config.assert_called_with('server-config', 'DEFAULT')
    assert constructor.call_args.kwargs['service_endpoint'] == client.endpoint
    first, second = [call.args[0] for call in sdk.chat.call_args_list]
    assert first.serving_mode.model_id == client.model_id
    assert first.compartment_id == 'ocid1.compartment.oc1..example'
    assert first.chat_request.max_completion_tokens == 2048
    assert first.chat_request.max_tokens is None
    assert first.chat_request.verbosity == 'MEDIUM'
    assert all(t.name != 'file_search' for t in first.chat_request.tools)
    assert second.chat_request.messages[-2].tool_calls[0].id == 'call1'
    assert second.chat_request.messages[-1].tool_call_id == 'call1'
    with pytest.raises(ValueError):
        client.create_response_sync([{'role': 'user', 'content': 'Policies'}], require_file_search=True)


def test_native_usage_and_incomplete_response():
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    from app.enterprise_ai.native_evaluation import NativeChatEvaluationClient
    client = NativeChatEvaluationClient(native_configuration(), config_file='server-config')
    choice = NS(finish_reason='stop', message=NS(content=[NS(text='Answer')], tool_calls=[]),
                usage=NS(prompt_tokens=33, completion_tokens=12))
    sdk = Mock(); sdk.chat.return_value = NS(data=NS(chat_response=NS(choices=[choice], usage=None)))
    with patch('oci.config.from_file', return_value={}), patch(
            'oci.generative_ai_inference.GenerativeAiInferenceClient', return_value=sdk):
        result = client.create_response_sync([{'role': 'user', 'content': 'Explain'}])
        assert result['usage']['input_tokens'] == 33
        assert result['usage']['output_tokens'] == 12
        choice.usage = None
        assert client.create_response_sync([{'role': 'user', 'content': 'Explain'}])['usage'] is None
        choice.finish_reason = 'length'
        with pytest.raises(RuntimeError):
            client.create_response_sync([{'role': 'user', 'content': 'Explain'}])


def test_responses_iam_uses_fixed_default_profile(context):
    container, actor = context
    data = configuration(); data.pop('api_key'); data['auth_mode'] = 'oci'
    model = RegionalEvaluationModel(**data)
    container.settings.oci_config_profile = 'UNRELATED'
    scoped = container.chat_experience_service.scoped_container(model.profile(), False, ChatTrace(model.profile()), actor, model)
    assert scoped.responses.client.responses_auth_mode == 'oci'
    assert scoped.responses.client.config_profile == 'DEFAULT'
    assert scoped.responses.client.auth_mode == 'api_key'
    assert scoped.responses.client._genai_api_key is None
    assert container.settings.oci_config_profile == 'UNRELATED'
    with pytest.raises(ValidationError):
        RegionalEvaluationModel(**(data | {'api_key': SECRET}))


def test_native_error_classification_redacts_provider_payload():
    import oci
    from unittest.mock import Mock
    from app.enterprise_ai.native_evaluation import NativeChatEvaluationClient, NativeChatUnavailable
    client = NativeChatEvaluationClient(native_configuration(), config_file='server-config')
    sdk = Mock()
    sdk.chat.side_effect = oci.exceptions.ServiceError(status=400, code='400', headers={},
        message='Function tools with reasoning_effort are not supported. secret-prompt-and-key')
    with patch('oci.config.from_file', return_value={}), patch(
            'oci.generative_ai_inference.GenerativeAiInferenceClient', return_value=sdk):
        with pytest.raises(NativeChatUnavailable) as error:
            client.create_response_sync([{'role': 'user', 'content': 'Explain'}])
    assert error.value.public_code == 'native_tools_require_responses'
    assert error.value.http_status == 400
    assert 'secret' not in str(error.value)
