"""Cloud retrieval, safe event metadata and model/tool attribution."""
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.chat_api import router
from app.chat_models import ChatModelProfile
from app.chat_telemetry import ChatTrace, MeasuredResponses, active_trace
from app.config import Settings
from app.oci.operations import CentralLogs, metadata
from app.proxy_routes import integrated_target
from app.session_api import retail_session


def test_cloud_reads_oci_with_bounded_window_and_filters_private_payloads():
    log = CentralLogs(Settings(_env_file=None, oci_application_log_id='ocid1.log.oc1.test', oci_application_log_group_id='ocid1.loggroup.oc1.test', oci_compartment_id='compartment'))
    client = Mock()
    client.search_logs.return_value.data.results = [SimpleNamespace(data={'logContent': {'data': json.dumps({
        'timestamp': '2026-10-15T03:58:15.893553+00:00', 'status': 200, 'logger': 'app.http',
        'event': 'request_completed', 'method': 'POST', 'message': 'secret prompt', 'api_key': 'secret',
        'mcp_tool': {'private_payload': 'secret'}, 'duration_ms': 25.5,
    })}})]
    with patch('app.oci.operations.sdk_client', return_value=client):
        result = log.search(hours=6)
    assert result['source'] == 'OCI Logging Search API' and result['search_status'] == 'available'
    assert len(result['events']) == 1 and result['events'][0]['status'] == 200
    assert result['events'][0]['timestamp'].endswith('+00:00')
    assert 'secret' not in json.dumps(result) and 'mcp_tool' not in result['events'][0]
    body = client.search_logs.call_args.args[0]
    assert (body.time_end-body.time_start).total_seconds() == 6*3600
    assert body.search_query == 'search "compartment/ocid1.loggroup.oc1.test/ocid1.log.oc1.test" | sort by datetime desc'
    assert client.search_logs.call_args.kwargs['limit'] == 50
    with pytest.raises(ValueError): log.search(hours=999)


def test_cloud_failure_is_not_reported_as_an_empty_success():
    log = CentralLogs(Settings(_env_file=None, oci_application_log_id='ocid1.log.oc1.test', oci_application_log_group_id='ocid1.loggroup.oc1.test', oci_compartment_id='compartment'))
    with patch('app.oci.operations.sdk_client', side_effect=Exception('private secret')):
        result = log.search()
    assert result['search_status'] == 'service_unavailable' and result['events'] == []
    assert 'private secret' not in json.dumps(result)
    assert CentralLogs(Settings(_env_file=None)).search()['search_status'] == 'not_configured'


def test_metadata_keeps_event_identity_without_formatted_messages():
    record = logging.LogRecord('app.http', logging.INFO, '', 1, 'request_completed', (), None)
    record.method, record.status, record.api_key, record.duration_ms = 'POST', 200, 'secret', float('nan')
    result = metadata(record)
    assert result['event'] == 'request_completed' and result['method'] == 'POST'
    assert 'api_key' not in result and 'duration_ms' not in result
    record.msg = 'private prompt'
    assert 'event' not in metadata(record)


@pytest.mark.parametrize('provider,mode,source', [(None, 'responses', 'oci_responses'), (None, 'native_chat', 'oci_chat'),
    ('gemini', 'responses', 'gemini'), ('openai', 'responses', 'openai_responses'), ('anthropic', 'responses', 'anthropic')])
def test_model_tool_requests_are_attributed_without_claiming_execution(provider, mode, source):
    client = SimpleNamespace(provider=provider, api_mode=mode, create_response_sync=Mock(return_value={
        'status': 'completed', 'tool_calls': [{'name': 'get_inventory_risk', 'arguments': '{"secret":1}'}]}))
    trace = ChatTrace(ChatModelProfile(id='test', label='Test', model_id='test-model'))
    token = active_trace.set(trace)
    try:
        MeasuredResponses(client, trace).create_response_sync([], tools=[{'name': 'get_inventory_risk'}])
    finally:
        active_trace.reset(token)
    assert trace.events[-1]['stage'] == 'model_tool_request'
    assert trace.events[-1]['api_source'] == source and trace.events[-1]['status'] == 'requested'
    assert not any(event['stage'] == 'function_tool' for event in trace.events)
    assert 'secret' not in json.dumps(trace.events)


def test_unoffered_model_tool_names_are_not_copied_into_telemetry():
    client = SimpleNamespace(provider='gemini', create_response_sync=Mock(return_value={
        'status': 'completed', 'tool_calls': [{'name': 'secret-from-model', 'arguments': '{}'}]}))
    trace = ChatTrace(ChatModelProfile(id='test', label='Test', model_id='test'))
    token = active_trace.set(trace)
    try:
        MeasuredResponses(client, trace).create_response_sync([], tools=[{'name': 'get_inventory_risk'}])
    finally:
        active_trace.reset(token)
    assert trace.events[-1]['tool'] == 'unavailable_tool'
    assert 'secret-from-model' not in json.dumps(trace.events)


def test_cloud_route_validates_window_and_hosted_proxy_allows_external_setup():
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[retail_session] = lambda: object()
    logs = Mock(); logs.search.return_value = {'events': []}
    with patch('app.chat_api.get_container', return_value=SimpleNamespace(central_logs=logs)), TestClient(app) as client:
        assert client.get('/chat/central-logs?hours=24').status_code == 200
        logs.search.assert_called_once_with(hours=24)
        assert client.get('/chat/central-logs?hours=1000').status_code == 422
    assert integrated_target('GET', 'chat/central-logs', 'hours=6') == 'chat/central-logs?hours=6'
    for path in ['external-models', 'external-model-options']:
        assert integrated_target('POST', f'chat/{path}', '') == f'chat/{path}'


@pytest.mark.parametrize('configured', [False, True])
def test_hosted_tracing_status_is_forwarded_without_project_credentials(configured):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[retail_session] = lambda: object()
    tracing = SimpleNamespace(is_configured=configured,
        base_url='https://tracing.example.invalid', environment='test',
        public_key='private-project-key', secret_key='private-secret-key')
    target = integrated_target('GET', 'chat/tracing', '')
    with patch('app.chat_api.get_container', return_value=SimpleNamespace(langfuse=tracing)), TestClient(app) as client:
        response = client.get('/' + target)
    assert response.status_code == 200
    assert response.json()['status'] == ('configured' if configured else 'disabled')
    assert response.json()['dashboard_url'] == (tracing.base_url if configured else None)
    assert 'private-project-key' not in response.text
    assert 'private-secret-key' not in response.text
    from fastapi import HTTPException
    for method, query in [('POST', ''), ('GET', 'secret_key=value')]:
        with pytest.raises(HTTPException):
            integrated_target(method, 'chat/tracing', query)
