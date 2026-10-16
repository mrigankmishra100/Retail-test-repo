"""Exercise authenticated worker activity across success/failure service boundaries."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from time import monotonic
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock, patch
from uuid import uuid4
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.agent_app import create_agent_app
from app.agents.remote import RemoteAgentClient
from app.config import Settings
from app.live_activity import activity_note, activity_span, active_activity
from app.remote_activity import capture_activity, export_activity, merge_activity
from app.services.sessions import SessionContext


def actor():
    return SessionContext(session_id=uuid4(), role='retail-manager',
                          expires_at=datetime.now(timezone.utc) + timedelta(hours=1))


@pytest.mark.parametrize('failed', [False, True])
def test_signed_agent_response_returns_private_worker_steps_even_on_failure(failed):
    session = actor()
    settings = Settings(_env_file=None, application_role='retail-agent', agent_service_key='x' * 43)
    container = SimpleNamespace(langfuse=None, central_logs=Mock(), close=Mock())
    app = create_agent_app(settings, container)

    def dispatch(*args):
        with activity_span('model', 'Generating response'):
            activity_note('security', 'Prompt injection check · input', 'passed')
            if failed:
                raise HTTPException(503, 'private provider error must not cross the boundary')
        return {'message': 'business answer', 'allowed_actions': [], 'llm_status': 'completed'}

    with TestClient(app) as http:
        class Opener:
            @contextmanager
            def open(self, request, **kwargs):
                response = http.post('/internal/agent/invoke', content=request.data, headers=dict(request.header_items()))
                if response.status_code >= 400:
                    raise HTTPError(request.full_url, response.status_code, 'failed', response.headers, BytesIO(response.content))
                yield SimpleNamespace(status=response.status_code, headers=response.headers,
                                      read=lambda size: response.content[:size])
        client = RemoteAgentClient(audience='retail',
            endpoint='https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.iad.fixture/actions/invoke',
            key='x' * 43)
        with patch('app.agent_app.dispatch', dispatch), patch('app.agents.remote.build_opener', return_value=Opener()):
            with capture_activity(session) as caller:
                if failed:
                    with pytest.raises(RuntimeError):
                        client.invoke('chat', {'request': {'message': 'private user prompt'}}, session)
                else:
                    assert client.invoke('chat', {'request': {'message': 'private user prompt'}}, session)['message'] == 'business answer'
                result = export_activity(caller)
    assert active_activity.get() is None
    assert [step['name'] for step in result['steps']] == [
        'Calling hosted agent', 'Generating response', 'Prompt injection check · input']
    assert result['steps'][1]['service'] == 'retail-agent'
    assert result['steps'][1]['parent_id'] == result['steps'][0]['id']
    assert result['steps'][1]['status'] == ('failed' if failed else 'completed')
    wire = json.dumps(result)
    assert 'private' not in wire and 'business answer' not in wire


def test_nested_mcp_activity_is_bounded_and_owned_by_caller():
    first, second = actor(), actor()
    with capture_activity(first) as caller:
        started = monotonic()
        with activity_span('tool', 'get_inventory_risk'):
            with capture_activity(second) as remote:
                activity_note('mcp_safety', 'get_inventory_risk', 'passed')
                child = export_activity(remote)
            merge_activity(child, service='mcp-server', started=started)
        assert caller.store.read(caller.id, second.session_id) is None
        rows = export_activity(caller)['steps']
        assert rows[1]['parent_id'] == rows[0]['id']
        assert rows[1]['service'] == 'mcp-server'
        # Arbitrary payload fields and oversized batches are ignored, not leaked.
        invalid = {'steps': [{**child['steps'][0], 'arguments': 'private secret'}]}
        merge_activity(invalid, service='mcp-server', started=started)
        merge_activity({'steps': child['steps'] * 201}, service='mcp-server', started=started)
        assert len(export_activity(caller)['steps']) == 2
        for _ in range(210):
            merge_activity(child, service='mcp-server', started=started)
        final = export_activity(caller)
        assert len(final['steps']) == 200 and final['truncated']


def test_mcp_control_evidence_reaches_chat_metrics_without_live_polling():
    from app.chat_telemetry import ChatTrace, active_trace
    from app.chat_models import ChatModelProfile
    trace = ChatTrace(ChatModelProfile(id='test', label='test', model_id='test'))
    with capture_activity(actor()) as worker:
        activity_note('mcp_safety', 'get_inventory_risk', 'passed', api_source='local_mcp')
        activity_note('mcp_result', 'get_inventory_risk', 'failed', error_code='invalid_tool_result')
        activity = export_activity(worker)
    token = active_trace.set(trace)
    try:
        merge_activity(activity, service='mcp-server', started=monotonic())
    finally:
        active_trace.reset(token)
    assert [event['status'] for event in trace.events] == ['passed', 'failed']
    assert trace.events[1]['error_code'] == 'invalid_tool_result'


def test_invalid_activity_entry_does_not_discard_other_worker_evidence():
    with capture_activity(actor()) as worker:
        activity_note('security', 'Prompt injection check', 'passed')
        activity_note('tool', 'unsupported\nname', 'failed')
        activity_note('mcp_result', 'get_inventory_risk', 'passed')
        exported = export_activity(worker)
    assert [step['stage'] for step in exported['steps']] == ['security', 'mcp_result']
    assert exported['truncated']
