"""Trace real local boundary decisions without inventing hosted MCP checks."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from typing import Annotated
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field
from app.chat_models import ChatModelProfile
from app.chat_telemetry import ChatTrace, MeasuredResponses, active_trace
from app.enterprise_ai.mcp_safety import EnterpriseAIMCPSafetyClient
from app.live_activity import RequestActivity, active_activity, store
from app.mcp.boundary import secured_tool
from app.observability.langfuse import LangfuseObservabilityClient
from app.services.sessions import SessionContext, SessionError

SupplierId = Annotated[str, Field(pattern=r'^SUP[0-9]{3}$')]
class Result(BaseModel):
    value: int

def session(role='retail-manager'):
    return SessionContext(session_id=uuid4(), role=role,
        supplier_id='SUP001' if role == 'supplier' else None,
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1))

@contextmanager
def recording(owner):
    trace = ChatTrace(ChatModelProfile(id='fixture', label='Fixture', model_id='fixture'))
    activity = RequestActivity(str(uuid4())); activity.bind(owner)
    t = active_trace.set(trace); a = active_activity.set(activity)
    try:
        yield trace, lambda: store.read(activity.id, owner.session_id)['steps']
    finally:
        active_trace.reset(t); active_activity.reset(a)
        store.finish(activity.id, 'completed')

def tool(owner, service, *, controlled=False, approved=False, enabled=False):
    container = SimpleNamespace(langfuse=LangfuseObservabilityClient(enabled=False), mcp_safety=EnterpriseAIMCPSafetyClient(enabled=enabled),
        case_service=SimpleNamespace(approval_context=Mock(return_value={
            'human_approved': approved, 'approval_event_ids': ['fixture'] if approved else []})))
    def get_supplier_policy(supplier_id: SupplierId) -> Result:
        return service(supplier_id)
    def send_supplier_request(case_id: str, supplier_id: SupplierId) -> Result:
        return service(supplier_id)
    def identity():
        if owner is None: raise SessionError('private session data')
        return owner
    return secured_tool(lambda: container, identity)(send_supplier_request if controlled else get_supplier_policy)

def test_success_records_precheck_before_execution_and_result_validation():
    owner = session()
    with recording(owner) as (trace, live):
        def business(_):
            assert [(e['stage'], e['status']) for e in trace.events] == [('mcp_safety','passed')]
            return {'value': 1}
        assert tool(owner, business)(supplier_id='SUP001').value == 1
        assert [(e['stage'], e['status']) for e in trace.events] == [('mcp_safety','passed'),('mcp_result','passed')]
        assert [e['stage'] for e in live()] == ['mcp_safety','mcp_result']
        assert all(e['api_source'] == 'local_mcp' for e in trace.events)
        assert 'SUP001' not in json.dumps(trace.events)

@pytest.mark.parametrize('scenario,code', [('session','session_required'), ('arguments','invalid_arguments'),
    ('scope','permission_denied'), ('approval','human_approval_required'), ('hosted','policy_check_unavailable')])
def test_blocked_checks_report_only_fixed_reason_and_never_execute(scenario,code):
    owner = session('supplier' if scenario == 'scope' else 'retail-manager')
    service = Mock(return_value={'value': 1})
    fn = tool(None if scenario == 'session' else owner, service,
        controlled=scenario=='approval', enabled=scenario=='hosted')
    args = {'supplier_id': 'private-invalid-value' if scenario=='arguments' else 'SUP002' if scenario=='scope' else 'SUP001'}
    if scenario=='approval': args['case_id'] = 'fixture'
    with recording(owner) as (trace, live), pytest.raises(ToolError):
        try: fn(**args)
        finally:
            assert len(trace.events) == 1
            assert trace.events[0]['error_code'] == code
            assert trace.events[0]['stage'] == 'mcp_safety'
            assert trace.events[0]['status'] == ('failed' if scenario=='hosted' else 'blocked')
            assert live()[0]['error_code'] == code
            assert 'private' not in json.dumps(trace.events)
            service.assert_not_called()

def test_approved_controlled_action_passes_actual_approval_gate():
    owner = session(); service = Mock(return_value={'value': 1})
    with recording(owner) as (trace, _):
        tool(owner,service,controlled=True,approved=True)(case_id='fixture',supplier_id='SUP001')
    service.assert_called_once()
    assert [e['status'] for e in trace.events] == ['passed','passed']

@pytest.mark.parametrize('failure', ['invalid_result','service_error'])
def test_service_failure_is_not_misreported_as_safety_failure(failure):
    owner = session()
    service = Mock(return_value={'value':'private-invalid-result'}) if failure=='invalid_result' else Mock(side_effect=RuntimeError('private service error'))
    with recording(owner) as (trace, _), pytest.raises(ToolError):
        try: tool(owner,service)(supplier_id='SUP001')
        finally:
            assert trace.events[0]['stage'] == 'mcp_safety' and trace.events[0]['status'] == 'passed'
            if failure=='invalid_result':
                assert trace.events[-1]['stage'] == 'mcp_result'
                assert trace.events[-1]['status'] == 'failed'
                assert trace.events[-1]['error_code'] == 'invalid_tool_result'
            else: assert len(trace.events) == 1
            assert 'private' not in json.dumps(trace.events)

@pytest.mark.parametrize('status',['completed','failed'])
def test_file_search_reports_responses_api_but_no_mcp_safety(status):
    owner = session()
    client = SimpleNamespace(api_mode='responses', create_response_sync=Mock(return_value={
        'status':'completed', 'builtin_tools':[{'type':'file_search_call','status':status}], 'tool_calls':[]}))
    with recording(owner) as (trace, live):
        MeasuredResponses(client, trace).create_response_sync([])
        assert trace.events[-1]['api_source'] == 'oci_responses'
        assert trace.events[-1]['status'] == status
        assert live()[-1]['api_source'] == 'oci_responses'
        assert not any(e['stage'].startswith('mcp_') for e in trace.events)
