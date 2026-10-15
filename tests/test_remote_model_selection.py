"""Signed HTTP MCP calls must not resolve an LLM for model-independent tools."""
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.config import Settings
from app.enterprise_ai.mcp_safety import EnterpriseAIMCPSafetyClient
from app.mcp import tools
from app.mcp.model_binding import model_binding_for_tool, MODEL_BOUND_TOOLS
from app.mcp.remote import assertion
from app.mcp.server import mcp_http_app
from app.services.sessions import SessionContext


@pytest.fixture
def remote():
    key = 'test-signing-key-' * 3
    session = SessionContext(session_id=uuid4(), role='retail-manager',
                             expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
    history = Mock()
    history.get.side_effect = lambda conversation_id, workflow_thread_id, **kw: dict(
        conversation_id=str(conversation_id), workflow_thread_id=None, exists=False, next_turn=1, messages=[])
    supplier = Mock()
    supplier.get_suppliers_for_item.return_value = []
    scope = Mock(side_effect=AssertionError('Data tools must not resolve a model'))
    container = SimpleNamespace(settings=Settings(_env_file=None, enterprise_ai_model_id='oci-baseline',
        remote_mcp_service_keys={'retail':key}), chat_history_service=history,
        supplier_service=supplier, langfuse=SimpleNamespace(observation=lambda *a, **k: nullcontext(Mock())), mcp_safety=EnterpriseAIMCPSafetyClient(enabled=False),
        chat_experience_service=SimpleNamespace(scoped_container=scope))
    @asynccontextmanager
    async def lifespan(app):
        async with mcp_http_app.lifespan(app):
            yield
    app = FastAPI(lifespan=lifespan)
    app.mount('/mcp', mcp_http_app)
    with patch.object(tools, 'get_container', return_value=container), TestClient(app) as client:
        def call(name, args, selection, *, signed_args=None, actor=None):
            actor = actor or session
            validated = tools.TOOL_FUNCTIONS[name].input_model.model_validate(signed_args or args).model_dump(mode='json')
            signed = assertion(name, validated, actor, key, selection)
            response = client.post('/mcp/', headers={'Accept':'application/json, text/event-stream','X-MCP-Assertion':signed},
                json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})
            assert response.status_code == 200
            return response.json()['result']
        yield call, container


@pytest.mark.parametrize('profile', ['external-gemini','external-openai','external-anthropic','other-region-model'])
def test_history_and_supplier_reads_accept_model_independent_signed_calls(remote, profile):
    call, container = remote
    selection = {'profile':profile,'model_id':'provider-model'}
    for name, args in [('get_chat_history',{'conversation_id':str(uuid4())}),
                       ('get_suppliers_for_item',{'item_id':'ITEM001'})]:
        result = call(name,args,selection)
        assert not result.get('isError'), result
    container.chat_history_service.get.assert_called_once()
    container.supplier_service.get_suppliers_for_item.assert_called_once_with('ITEM001')
    container.chat_experience_service.scoped_container.assert_not_called()


@pytest.mark.parametrize('name,args', [
    ('prepare_workflow_case',{'case_id':str(uuid4()),'supplier_id':'SUP001','version':0,'idempotency_key':'prepare-test'}),
    ('search_policy_documents',{'query':'supplier policy'}),
    ('query_retail_data',{'dataset':'inventory'})])
def test_unknown_model_still_rejected_for_ai_dependent_capabilities(remote, name, args):
    call, _ = remote
    result = call(name,args,{'profile':'external-test','model_id':'provider-model'})
    assert result['isError']
    assert 'validation_error' in json.dumps(result)


def test_tampered_data_request_still_rejected(remote):
    call, container = remote
    result = call('get_suppliers_for_item',{'item_id':'ITEM002'},
        {'profile':'external-test','model_id':'provider-model'},signed_args={'item_id':'ITEM001'})
    assert result['isError'] and 'forbidden' in json.dumps(result)
    container.supplier_service.get_suppliers_for_item.assert_not_called()


def test_sender_only_delegates_model_identity_for_capabilities_that_need_it():
    selection = {'profile':'external-test','model_id':'test','session_id':'private-context'}
    for name in tools.TOOL_FUNCTIONS:
        expected = {'profile':'external-test','model_id':'test'} if name in MODEL_BOUND_TOOLS else None
        assert model_binding_for_tool(name,selection) == expected


def test_source_image_catalog_is_shared_and_preserves_baseline():
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[1]/'backend/Dockerfile.source').read_text()
    values = [json.loads(line.split("'",2)[1]) for line in raw.splitlines() if line.startswith('ENV CHAT_MODEL_PROFILES=')]
    assert len(values) == 2 and values[0] == values[1]
    assert values[0][0]['id'] == 'baseline'
    assert [p['model_id'] for p in values[0]] == ['google.gemini-2.5-flash','openai.gpt-oss-120b']
