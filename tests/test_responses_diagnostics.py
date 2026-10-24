"""Offline transport diagnostics: preserve failures while protecting request data."""
import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from app.enterprise_ai import responses_transport as transport_module
from app.enterprise_ai.responses_transport import OCIResponsesTransport, OCIResponsesUnavailable, MAX_ERROR_BYTES
from app.utils.logger import JsonFormatter


PROJECT = 'ocid1.generativeaiproject.oc1.iad.example'
SECRET = 'sk-private-test-key'


def transport():
    return OCIResponsesTransport(
        endpoint='https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com',
        project_id=PROJECT, responses_auth_mode='genai_api_key', genai_api_key=SECRET)


def payload():
    return {'model': 'google.gemini-2.5-flash',
            'input': [{'role': 'user', 'content': 'private inventory question'}],
            'instructions': 'private system instruction',
            'tools': [{'type': 'file_search', 'vector_store_ids': ['vs_test']},
                      {'type': 'function', 'name': 'query_retail_data'}],
            'tool_choice': {'type': 'allowed_tools', 'mode': 'required',
                            'tools': [{'type': 'file_search'}]},
            'include': ['file_search_call.results'], 'max_output_tokens': 6000,
            'parallel_tool_calls': False, 'store': False}


@pytest.mark.parametrize('status,code', [(400, 'request_rejected'), (500, 'provider_unavailable')])
@pytest.mark.parametrize('nested', [False, True])
def test_error_details_survive_json_formatter_without_changing_public_error(monkeypatch, caplog, status, code, nested):
    error = {'code': 'InvalidParameter', 'type': 'invalid_request_error', 'param': 'tool_choice',
             'message': 'Unsupported tool_choice for file_search', 'debug': 'private extra content'}
    stream = io.BytesIO(json.dumps({'error': error} if nested else error).encode())
    failure = HTTPError('https://example.invalid', status, 'private HTTP reason',
                        {'opc-request-id': '/request/123'}, stream)
    opener = Mock(side_effect=failure)
    monkeypatch.setattr(transport_module, 'build_opener', lambda *args: SimpleNamespace(open=opener))
    with caplog.at_level(logging.INFO), pytest.raises(OCIResponsesUnavailable) as caught:
        transport().request('POST', '/responses', payload())
    assert caught.value.http_status == status
    assert caught.value.public_code == code
    assert str(caught.value) == 'OCI Responses service unavailable'
    assert opener.call_count == 1  # No automatic retry or fallback.
    assert stream.closed
    record = caplog.records[-1]
    event = json.loads(JsonFormatter().format(record))
    assert event['level'] == 'ERROR'
    assert event['http_status'] == status
    assert event['opc_request_id'] == '/request/123'
    assert event['oci_provider_error'] == {'body_status': 'json', **{k: error[k] for k in ('code', 'type', 'param', 'message')}}
    context = event['oci_response_context']
    assert context['project_id'] == PROJECT
    assert context['model_id'] == 'google.gemini-2.5-flash'
    assert context['file_search_enabled'] is True
    assert context['vector_store_ids'] == ['vs_test']
    assert context['tool_choice'] == {'type': 'allowed_tools', 'mode': 'required'}
    assert 'private' not in json.dumps(event)
    assert SECRET not in json.dumps(event)


def test_provider_error_redacts_credentials_echoed_content_and_quoted_fragments():
    body = payload()
    body['input'].append({'type': 'function_call_output', 'output': 'private tool result'})
    error = {'message': f'Bad private inventory question / private system instruction / private tool result; '
                        f'Bearer {SECRET}; password="secretvalue with spaces"; "partial policy excerpt"; api_key=abc123',
             'code': SECRET, 'param': {'private': 'object'}}
    result = transport_module._provider_error(io.BytesIO(json.dumps({'error': error}).encode()),
                                              body=body, secrets=[SECRET])
    text = json.dumps(result)
    for secret in ('private inventory', 'private system', 'private tool', SECRET,
                   'secretvalue', 'with spaces', 'partial policy excerpt', 'abc123'):
        assert secret not in text
    assert 'code' not in result and 'param' not in result
    assert '[REDACTED' in text


@pytest.mark.parametrize('raw,expected', [(b'', 'empty'), (b'<html>private gateway response</html>', 'non_json'),
                                        (b'[]', 'unrecognized'), (b'x' * (MAX_ERROR_BYTES + 10), 'too_large')])
def test_unusable_error_bodies_are_omitted(raw, expected):
    assert transport_module._provider_error(io.BytesIO(raw)) == {'body_status': expected}


def test_failed_error_body_read_does_not_replace_http_status(monkeypatch, caplog):
    failure = HTTPError('https://example.invalid', 500, 'private', {}, io.BytesIO())
    failure.read = Mock(side_effect=TimeoutError('private'))
    monkeypatch.setattr(transport_module, 'build_opener', lambda *args: SimpleNamespace(open=Mock(side_effect=failure)))
    with pytest.raises(OCIResponsesUnavailable) as caught:
        transport().request('POST', '/responses', payload())
    assert caught.value.http_status == 500
    assert caplog.records[-1].oci_provider_error == {'body_status': 'read_failed'}


def test_success_preserves_result_and_logs_context_without_response_body(monkeypatch, caplog):
    class Response(io.BytesIO):
        status = 200
        headers = {'opc-request-id': '/success/123'}
    result = {'status': 'completed', 'output': 'private policy answer'}
    opener = Mock(return_value=Response(json.dumps(result).encode()))
    monkeypatch.setattr(transport_module, 'build_opener', lambda *args: SimpleNamespace(open=opener))
    with caplog.at_level(logging.INFO):
        assert transport().request('POST', '/responses', payload()) == result
    event = json.loads(JsonFormatter().format(caplog.records[-1]))
    assert event['level'] == 'INFO'
    assert event['oci_provider_error'] is None
    assert 'private' not in json.dumps(event)
    assert json.loads(opener.call_args.args[0].data) == payload()


def test_context_does_not_fail_on_optional_null_values():
    result = transport_module._request_context({'include': None, 'tools': None}, PROJECT)
    assert result['file_search_enabled'] is False
    assert result['include'] == []
