from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
import json
import pytest
from app.agents.retail_conversation import run_conversation, MAX_MODEL_CALLS, MAX_TOOL_CALLS
from app.agents.topic_scope import check_topic_scope
from app.services.retail_conversation import RetailConversationService
from app.services.sessions import SessionContext
from app.config import Settings
from app.enterprise_ai.responses import EnterpriseAIResponsesClient


def tools():
    return SimpleNamespace(session=SessionContext(session_id=uuid4(), role='retail-manager',
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1)),
        container=SimpleNamespace(settings=Settings(_env_file=None)), call=Mock(return_value={'items': []}))


def call(index):
    return {'status': 'completed', 'output': '', 'tool_calls': [
        {'id': str(index), 'name': 'get_suppliers_for_item', 'arguments': '{"item_id":"ITEM001"}'}]}


def test_analysis_finishes_after_more_than_four_model_calls():
    client, toolset = Mock(), tools()
    client.create_response_sync.side_effect = [call(i) for i in range(6)] + [
        {'status': 'completed', 'output': 'Comparison based on returned data.', 'tool_calls': []}]
    result = run_conversation(responses=client, tools=toolset, message='Compare stock coverage and suppliers')
    assert result['llm_status'] == 'completed' and result['model_count'] == 7
    assert toolset.call.call_count == 6


def test_last_model_call_is_reserved_for_synthesis_and_has_tool_evidence():
    client, toolset = Mock(), tools()
    client.create_response_sync.side_effect = [call(i) for i in range(MAX_MODEL_CALLS - 1)] + [
        {'status': 'completed', 'output': 'Supplier information is incomplete.', 'tool_calls': []}]
    result = run_conversation(responses=client, tools=toolset, message='Compare suppliers')
    assert result['llm_status'] == 'completed' and result['model_count'] == MAX_MODEL_CALLS
    final = client.create_response_sync.call_args
    assert final.kwargs == {'tools': [], 'allow_tools': False}
    assert any(turn['role'] == 'tool' for turn in final.args[0])
    assert 'missing information' in final.args[0][-1]['content']


def test_tool_budget_is_bounded_and_preserves_a_final_answer_call():
    client, toolset = Mock(), tools()
    batch = {'status': 'completed', 'output': '', 'tool_calls': [call(i)['tool_calls'][0] for i in range(MAX_TOOL_CALLS)]}
    client.create_response_sync.side_effect = [batch,
        {'status': 'completed', 'output': 'Evidence summary.', 'tool_calls': []}]
    result = run_conversation(responses=client, tools=toolset, message='Compare suppliers')
    assert toolset.call.call_count == MAX_TOOL_CALLS
    assert result['llm_status'] == 'completed'
    assert client.create_response_sync.call_args.kwargs['allow_tools'] is False


@pytest.mark.parametrize('decision,status', [('OUT_OF_SCOPE','completed'), ('CLARIFY','completed'), ('bad model output','unavailable')])
def test_topic_decision_never_exposes_classifier_prose_or_executes_tools(decision, status):
    client = Mock(enabled=True)
    client.create_response_sync.return_value = {'status': 'completed', 'output': decision, 'tool_calls': []}
    service = RetailConversationService(SimpleNamespace(settings=Settings(_env_file=None), responses=client, langfuse=None))
    with patch('app.services.retail_conversation.run_conversation') as graph:
        result = service._run(session=tools().session, message='What is the capital of India?', check_scope=True)
    graph.assert_not_called()
    assert result['llm_status'] == status and not result['used_tools']
    assert decision not in result['answer']
    assert client.create_response_sync.call_args.kwargs == {'tools': [], 'allow_tools': False}


def test_scope_allows_project_explanations_and_contextual_followups():
    client = Mock()
    client.create_response_sync.return_value = {'status': 'completed', 'output': 'IN_SCOPE'}
    history = [{'role':'user', 'content':'Explain the MCP checks in our retail app'},
               {'role':'assistant', 'content':'The boundary verifies roles and arguments.'}]
    assert check_topic_scope(client, 'And how do those work after deployment?', history) is None
    sent = client.create_response_sync.call_args.args[0]
    assert json.loads(sent[-1]['content'])['recent_history'] == history
    assert 'workflows' in sent[0]['content'] and 'architecture' in sent[0]['content']


def test_scope_rejects_unexpected_tool_calls_without_running_them():
    client = Mock()
    client.create_response_sync.return_value = {**call(1), 'output': 'IN_SCOPE'}
    assert check_topic_scope(client, 'Inventory')['llm_status'] == 'unavailable'


def test_oci_gpt_oss_receives_trusted_system_input_even_for_hosted_continuations():
    client = EnterpriseAIResponsesClient(model_id='openai.gpt-oss-120b')
    client.transport = Mock()
    client.transport.request.return_value = {'status': 'completed', 'output': [
        {'type':'message', 'role':'assistant', 'content':[{'type':'output_text','text':'IN_SCOPE'}]}]}
    client._create_responses_response([{'role':'system','content':'Trusted scope rules'},
        {'role':'user','content':'Untrusted question'}], allow_tools=False)
    payload = client.transport.request.call_args.args[2]
    assert payload['input'][0] == {'role':'system','content':'Trusted scope rules'}
    assert payload['input'][1] == {'role':'user','content':'Untrusted question'}
    assert payload['instructions'] == 'Trusted scope rules'
