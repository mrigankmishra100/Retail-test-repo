"""Selection persistence, native retrieval evidence and official cloud boundaries."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock
import json
import logging
import pytest

from app.config import Settings
from app.dependencies import ApplicationContainer
from app.services.sessions import SessionContext
from app.services.cases import CaseNotFound
from app.services.retail_workflow import RetailWorkflowService
from app.enterprise_ai.responses import EnterpriseAIResponsesClient
from app.enterprise_ai.responses_transport import OCIResponsesUnavailable
from app.oci.operations import OfficialCosts, CentralLogs, metadata
from app.mcp.remote import assertion, verify_assertion
from app.agent_app import ReplayGuard
from app.proxy_routes import integrated_target


def actor():
    return SessionContext(session_id=uuid4(), role='retail-manager', expires_at=datetime.now(timezone.utc)+timedelta(hours=1))


@pytest.fixture
def container(tmp_path):
    c = ApplicationContainer(Settings(_env_file=None, enterprise_ai_model_id='baseline',
        chat_metrics_path=tmp_path/'metrics.sqlite3', chat_model_profiles=[
            {'id':'gemini', 'label':'Gemini', 'model_id':'google.gemini-2.5-flash'},
            {'id':'gpt', 'label':'GPT OSS', 'model_id':'openai.gpt-oss-120b'}]))
    yield c
    c.close()


def test_model_switch_persists_next_calls_without_mutating_baseline_or_saved_state(container, monkeypatch):
    calls, snapshots = [], {}
    narrative = json.dumps({'request_summary':'Please review the replenishment request.',
                            'response_summary':'Please review the supplier response.'})
    def response(client, *args, **kwargs):
        calls.append(client.model_id)
        return {'status':'completed', 'output':narrative, 'usage':{'input_tokens':10,'output_tokens':10}}
    monkeypatch.setattr(EnterpriseAIResponsesClient, 'create_response_sync', response)
    class Graph:
        def __init__(self, **deps): self.deps=deps
        def invoke(self, command, config, **kwargs):
            # Exercise both dependencies used by actual explain and prepare nodes.
            self.deps['conversation'].container.responses.create_response_sync([{'role':'user','content':'Explain'}])
            self.deps['cases'].email_drafts.narratives.generate('Test supplier')
            snapshots.setdefault(config['configurable']['thread_id'], {'status':'awaiting_selection','message':'Review'})
        def get_state(self, config):
            return SimpleNamespace(values=snapshots.get(config['configurable']['thread_id'], {}), tasks=[], next=('next',))
    monkeypatch.setattr('app.services.retail_workflow.build_retail_graph', lambda **deps: Graph(**deps))
    session=actor()
    service=RetailWorkflowService(container)
    first=service.start({'model_profile':'gemini'},session=session,key='same')
    before=json.dumps(snapshots,sort_keys=True)
    switched=service.change_model(str(first.thread_id),'gpt',session=session)
    assert switched.model_id=='openai.gpt-oss-120b'
    assert json.dumps(snapshots,sort_keys=True)==before
    # A new service instance must restore selection, rather than use the baseline.
    restored=RetailWorkflowService(container)
    restored.resume(str(first.thread_id),session=session)
    assert calls==['google.gemini-2.5-flash']*2+['openai.gpt-oss-120b']*2
    assert container.responses.model_id=='baseline'
    assert container.case_service.email_drafts.narratives.responses is container.responses
    assert service.start({'model_profile':'gemini'},session=session,key='same').model_profile=='gpt'
    with pytest.raises(CaseNotFound):
        service.change_model(str(first.thread_id),'gemini',session=actor())
    with pytest.raises(ValueError):
        service.change_model(str(first.thread_id),'external',session=session)


def test_native_search_is_required_and_missing_evidence_fails():
    c=EnterpriseAIResponsesClient(enabled=True, endpoint='https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com',
        model_id='openai.gpt-oss-120b', project_id='ocid1.generativeaiproject.oc1.ap-hyderabad-1.example',vector_store_ids=['vs_test'])
    c.transport=Mock()
    message={'type':'message','role':'assistant','content':[{'type':'output_text','text':'A policy answer.'}]}
    c.transport.request.return_value={'status':'completed','output':[message]}
    with pytest.raises(OCIResponsesUnavailable) as error:
        c.create_response_sync([{'role':'user','content':'Policy?'}],require_file_search=True)
    assert error.value.public_code=='file_search_not_executed'
    payload=c.transport.request.call_args.args[2]
    assert payload['tool_choice']=={'type':'allowed_tools','mode':'required','tools':[{'type':'file_search'}]}
    c.transport.request.return_value={'status':'completed','output':[{'type':'file_search_call','status':'completed'},message]}
    assert c.create_response_sync([{'role':'user','content':'Policy?'}],require_file_search=True)['builtin_tools']


def test_signed_mcp_model_binding_verified_and_tamper_rejected():
    key='k'*43
    context=actor()
    selected={'profile':'gpt','model_id':'openai.gpt-oss-120b'}
    token=assertion('get_inventory_risk',{},context,key,selected)
    result,binding=verify_assertion(token,{'retail':key},'get_inventory_risk',{},ReplayGuard(),include_model=True)
    assert binding==selected and result.session_id==context.session_id
    with pytest.raises(PermissionError):
        verify_assertion(token,{'retail':key},'get_inventory_risk',{'days':3},ReplayGuard(),include_model=True)


def test_native_retrieval_continues_to_live_tools_for_mixed_questions():
    from app.agents.retail_conversation import run_conversation
    responses=Mock()
    responses.create_response_sync.side_effect=[
        {'status':'completed','output':'Policy lead time is three days.','tool_calls':[]},
        {'status':'completed','output':'','tool_calls':[{'id':'call-1','name':'get_inventory_risk','arguments':'{}'}]},
        {'status':'completed','output':'Compare live coverage with the policy lead time.','tool_calls':[]}]
    tools=SimpleNamespace(session=actor(),call=Mock(return_value={'current_stock':20}),
        container=SimpleNamespace(settings=SimpleNamespace(enterprise_ai_file_search_enabled=True,enterprise_ai_nl2sql_enabled=False)))
    result=run_conversation(responses=responses,tools=tools,message='Compare policy lead times with current stock coverage')
    assert result['model_count']==3 and result['used_tools']==['get_inventory_risk']
    assert responses.create_response_sync.call_args_list[0].kwargs['require_file_search']
    assert 'require_file_search' not in responses.create_response_sync.call_args_list[1].kwargs
    tools.call.assert_called_once_with('get_inventory_risk',days=None)


@pytest.mark.parametrize('conversation_id', [None, 'conv_test'])
def test_native_search_excerpts_reach_answer_call(conversation_id):
    """Search can complete without exposing its evidence in assistant prose."""
    from app.agents.retail_conversation import run_conversation
    c = EnterpriseAIResponsesClient(enabled=True,
        endpoint='https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com',
        model_id='google.gemini-2.5-flash',
        project_id='ocid1.generativeaiproject.oc1.ap-hyderabad-1.example', vector_store_ids=['vs_test'])
    c.transport = Mock()
    def message(value):
        return {'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': value}]}
    match = {'file_id': 'file_sup001', 'filename': 'SUP001_policy.pdf',
             'text': 'ITEM001: Standard delivery 3 days; expedited surcharge 5%.',
             'attributes': {'private': 'do not forward'}}
    def respond(method, path, payload):
        if c.transport.request.call_count == 1:
            assert payload['include'] == ['file_search_call.results']
            return {'status': 'completed', 'output': [
                {'type': 'file_search_call', 'status': 'completed', 'results': [match, match]},
                message('Search completed.')]}
        excerpts = [i['content'] for i in payload['input'] if i.get('role') == 'user'
                    and 'Retrieved policy excerpts' in i.get('content', '')]
        assert len(excerpts) == 1
        assert excerpts[0].count(match['text']) == 1
        assert 'SUP001_policy.pdf' in excerpts[0]
        assert 'do not forward' not in excerpts[0]
        assert all('results' not in i for i in payload['input'])
        return {'status': 'completed', 'output': [message('SUP001: 3 days, 5% (SUP001_policy.pdf).')]}
    c.transport.request.side_effect = respond
    tools = SimpleNamespace(session=actor(), call=Mock(), container=SimpleNamespace(
        settings=SimpleNamespace(enterprise_ai_file_search_enabled=True, enterprise_ai_nl2sql_enabled=False)))
    result = run_conversation(responses=c, tools=tools, message='Compare policy terms', conversation_id=conversation_id)
    assert result['llm_status'] == 'completed'
    assert result['answer'] == 'SUP001: 3 days, 5% (SUP001_policy.pdf).'
    assert result['model_count'] == 2
    tools.call.assert_not_called()


def test_native_evidence_is_bounded_without_inventing_citations():
    c = EnterpriseAIResponsesClient(vector_store_ids=['vs_test'])
    raw = {'status': 'completed', 'output': [{'type': 'file_search_call', 'status': 'completed',
        'results': [{'filename': 'large.pdf', 'text': 'x' * 20001},
                    {'filename': 'small.pdf', 'text': 'Delivery takes three days.'}]}]}
    result = c._parse_responses_result(raw, None)
    assert result['retrieval_evidence'] == {'excerpts': [
        {'filename': 'small.pdf', 'text': 'Delivery takes three days.'}], 'omitted': True}
    assert result['citations'] == []
    assert 'results' in raw['output'][0]  # Parsing does not mutate the provider response.


def test_official_costs_no_fake_zero_or_estimate_and_failure_is_cached(monkeypatch):
    service=OfficialCosts(Settings(_env_file=None,oci_finops_enabled=True))
    fetch=Mock(side_effect=SimpleOCIError())
    monkeypatch.setattr(service,'_fetch',fetch)
    result=service.read()
    assert result['status']=='access_denied_or_not_found' and result['totals']==[]
    assert service.read()==result
    fetch.assert_called_once()


class SimpleOCIError(Exception): status=404


def test_official_costs_aggregate_currencies_and_paginate(monkeypatch):
    import oci
    now=datetime.now(timezone.utc)
    def item(amount,currency):
        return SimpleNamespace(computed_amount=amount,currency=currency,service='Generative AI',time_usage_started=now)
    sdk=Mock()
    sdk.request_summarized_usages.side_effect=[
        SimpleNamespace(data=SimpleNamespace(items=[item('1.23','USD')]),headers={'opc-next-page':'next'}),
        SimpleNamespace(data=SimpleNamespace(items=[item('0.77','USD'),item('9','INR')]),headers={})]
    monkeypatch.setattr('app.oci.operations.sdk_client',lambda *args,**kwargs:sdk)
    s=Settings(_env_file=None,oci_finops_enabled=True,oci_usage_tenancy_id='tenant',oci_compartment_id='compartment')
    result=OfficialCosts(s).read()
    assert result['status']=='available'
    assert result['totals']==[{'currency':'USD','amount':'2.00'},{'currency':'INR','amount':'9'}]
    body=sdk.request_summarized_usages.call_args.args[0]
    assert body.query_type=='COST' and body.filter.dimensions[0].value=='compartment'
    assert sdk.request_summarized_usages.call_args.kwargs=={'page':'next'}


def test_central_logging_excludes_payloads_and_reports_delivery_failure():
    record=logging.makeLogRecord({'name':'app.chat','levelname':'INFO','msg':'secret prompt and credential',
        'stage':'model','status':'completed','trace_id':str(uuid4()),'model_id':'model-a','prompt':'secret',
        'exception':'secret','session_id':'private'})
    row=metadata(record)
    assert 'secret' not in json.dumps(row) and 'session_id' not in row
    sink=CentralLogs(Settings(_env_file=None))
    sink.emit(record)
    assert sink.queue.get_nowait()['stage']=='model'
    assert sink.status()['accepted_events']==0
    sink.close()


@pytest.mark.parametrize('method,path',[
    ('GET','chat/official-costs'),('GET','chat/central-logs'),('GET','chat/routing'),
    ('POST','retail/workflows/test-thread/model'),('GET','chat/models'),('GET','chat/metrics')])
def test_hosted_proxy_permits_integrated_routes(method,path):
    assert integrated_target(method,path,'')==path
