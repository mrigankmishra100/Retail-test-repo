"""Delivery capability changes take effect in the same supplier chat."""
from unittest.mock import patch
import pytest
from app.contracts import ChatRequest
from app.services.delivery_agent import validate_delivery_url
from app.services.supplier_conversation import SupplierConversationService
from tests.test_delivery_agent import setup, rest_stub, TOKEN


@pytest.mark.parametrize('question', [
    'explain delivery details or options', 'explain blue dart details',
    'can you tell distributor or delivery options available?',
    'Use the registered delivery agent to compare BlueDart, Delhivery, Ekart and DTDC',
    'Delivery options for postal code 500001?',
])
def test_connect_after_scope_answer_works_in_same_chat_without_refresh(rest_stub, question):
    c, who, row, service = setup()
    chat = SupplierConversationService(c)
    first = chat.respond(ChatRequest(message=question), session=who)
    assert first['mode'] == 'scope_guidance'
    assert not any(word in first['message'].lower() for word in ('connect', 'register', 'token', 'api'))
    assert not c.cache.get_state('supplier_chat:' + first['conversation_id'])['messages']
    service.connect(row['agent_id'], TOKEN, session=who)
    second = chat.respond(ChatRequest(message=question, intent='conversation', conversation_id=first['conversation_id']), session=who)
    assert second['mode'] == 'delivery_answer'
    assert second['used_tools'] == ['ask_delivery_agent']
    assert rest_stub[0][0][2] == {'question': question}
    c.responses.create_response_sync.assert_not_called()


def test_old_unavailable_history_cannot_override_current_connection(rest_stub):
    c, who, row, service = setup()
    chat = SupplierConversationService(c)
    first = chat.respond(ChatRequest(message='Hello'), session=who)
    key = 'supplier_chat:' + first['conversation_id']
    saved = c.cache.get_state(key)
    saved['messages'] = [{'role':'user', 'content':'Delivery options?'},
                         {'role':'assistant', 'content':'Delivery lookup is not available in this session.'}]
    c.cache.set_state(key, saved, ttl_seconds=300)
    service.connect(row['agent_id'], TOKEN, session=who)
    result = chat.respond(ChatRequest(message='Delivery options?', intent='conversation', conversation_id=first['conversation_id']), session=who)
    assert result['mode'] == 'delivery_answer'
    c.responses.create_response_sync.assert_not_called()


def test_delivery_transport_failure_is_safe_and_not_saved_as_model_history(rest_stub):
    c, who, row, service = setup()
    service.connect(row['agent_id'], TOKEN, session=who)
    with patch('app.agents.tool_access.SupplierToolAccess.call', side_effect=RuntimeError(TOKEN)):
        result = SupplierConversationService(c).respond(ChatRequest(message='Delivery options?'), session=who)
    assert result['llm_status'] == 'unavailable'
    assert TOKEN not in str(result)
    assert not c.cache.get_state('supplier_chat:' + result['conversation_id'])['messages']


def test_oci_hosted_path_accepts_dotted_ocid():
    url = ('https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/20251112/'
           'hostedApplications/ocid1.generativeaihostedapplication.oc1.iad.example/actions/invoke/api/v1/answers')
    assert validate_delivery_url(url).geturl() == url


@pytest.mark.parametrize('path', ['/../answers', '/api/./answers', '/api/%2e%2e/answers'])
def test_dotted_endpoint_does_not_allow_traversal(path):
    with pytest.raises(ValueError):
        validate_delivery_url('https://delivery.example.com' + path)
