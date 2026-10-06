"""Offline release regressions; runnable with standard-library unittest."""
import json
import asyncio
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastmcp.exceptions import ToolError
from app.agent_app import ReplayGuard
from app.agents.remote_contract import AgentInvocation, wrap_transport
from app.agents.retail_conversation import run_conversation
from app.config import Settings
from app.enterprise_ai.nl2sql import EnterpriseAINL2SQLClient, POC_NL2SQL_TABLES
from app.enterprise_ai.responses import EnterpriseAIResponsesClient
from app.enterprise_ai.mcp_safety import EnterpriseAIMCPSafetyClient
from app.mcp.remote import assertion, verify_assertion, _mac, arguments_digest
from app.oci.cache import OCICacheClient
from app.registry_api import router, registry_service, invoke
from app.services.agent_registry import RegistryConflict
from app.services.retail_conversation import RetailConversationService
from app.services.sessions import SessionContext
from app.session_api import current_session
from app.utils.logger import JsonFormatter
from app.utils.safe_diagnostics import protocol_details


def session(role='retail-manager'):
    return SessionContext(session_id=uuid4(), role=role,
        supplier_id='SUP001' if role == 'supplier' else None,
        expires_at=datetime.now(timezone.utc)+timedelta(minutes=10))


class ReleaseTests(unittest.TestCase):
    def test_shared_case_tools_work_for_both_roles_without_widening_private_tools(self):
        safety = EnterpriseAIMCPSafetyClient()
        for role in ('retail-manager', 'supplier'):
            context = {'role': role, 'session_id': str(uuid4()), 'supplier_id': 'SUP001' if role == 'supplier' else None}
            for tool in ('get_workflow_case', 'get_case_approved_memory'):
                self.assertTrue(asyncio.run(safety.validate_tool_call(tool, context))['allowed'])
            private_tool = 'get_supplier_inventory' if role == 'retail-manager' else 'get_inventory_risk'
            self.assertFalse(asyncio.run(safety.validate_tool_call(private_tool, context))['allowed'])

    def test_responses_is_default(self):
        self.assertEqual(Settings(_env_file=None).enterprise_ai_api_mode, 'responses')

    def test_native_chat_cannot_be_enabled(self):
        with self.assertRaises(ValueError):
            EnterpriseAIResponsesClient(enabled=True, api_mode='chat')
        with self.assertRaises(ValueError):
            Settings(_env_file=None, enterprise_ai_responses_enabled=True, enterprise_ai_api_mode='chat')

    def test_disabled_ai_returns_explicit_status(self):
        result = EnterpriseAIResponsesClient().create_response_sync([{'role': 'user', 'content': 'Hello'}])
        self.assertEqual(result['status'], 'not_configured')

    def test_incomplete_enabled_ai_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, 'configuration'):
            EnterpriseAIResponsesClient(enabled=True).create_response_sync([{'role': 'user', 'content': 'Hello'}])

    def test_model_and_file_search_use_responses(self):
        client = EnterpriseAIResponsesClient(enabled=True,
            endpoint='https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1',
            model_id='google.gemini-2.5-flash', project_id='ocid1.generativeaiproject.oc1.ap-hyderabad-1.example',
            responses_auth_mode='genai_api_key', genai_api_key='sk-test-only', vector_store_ids=['vs_test'])
        transport = Mock()
        transport.request.return_value = {'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Answer'}]}]}
        client.__dict__['transport'] = transport
        client._create_chat_response = Mock(side_effect=AssertionError('native chat must not run'))
        self.assertEqual(client.create_response_sync([{'role': 'user', 'content': 'Question'}])['output'], 'Answer')
        args = transport.request.call_args.args
        self.assertEqual(args[:2], ('POST', '/responses'))
        self.assertEqual(args[2]['tools'][0]['type'], 'file_search')
        client._create_chat_response.assert_not_called()

    def test_long_chat_body_uses_small_authenticated_header(self):
        actor, key = session(), 'x'*43
        args = {'user_message': 'Private chat '*1000, 'assistant_message': 'Reply '*2000}
        header = assertion('save_chat_turn', args, actor, key)
        self.assertLess(len(header), 2000)
        guard = ReplayGuard()
        self.assertEqual(verify_assertion(header, {'retail': key}, 'save_chat_turn', args, guard), actor)
        with self.assertRaises(PermissionError):
            verify_assertion(header, {'retail': key}, 'save_chat_turn', args, guard)

    def test_header_digest_rejects_body_changes(self):
        args, key = {'user_message': 'Original'}, 'x'*43
        header = assertion('save_chat_turn', args, session(), key)
        with self.assertRaises(PermissionError):
            verify_assertion(header, {'retail': key}, 'save_chat_turn', {'user_message': 'Changed'}, ReplayGuard())

    def test_header_rejects_wrong_role_and_key(self):
        args, key = {'dataset': 'inventory'}, 'x'*43
        header = assertion('query_retail_data', args, session('supplier'), key)
        with self.assertRaises(PermissionError):
            verify_assertion(header, {'supplier': key}, 'query_retail_data', args, ReplayGuard())
        with self.assertRaises(PermissionError):
            verify_assertion(header, {'supplier': 'y'*43}, 'query_retail_data', args, ReplayGuard())

    def test_legacy_signed_requests_remain_accepted(self):
        actor, key, args = session(), 'x'*43, {'days': 30}
        envelope = AgentInvocation(audience='retail', operation='get', nonce=uuid4(),
            issued_at=int(datetime.now(timezone.utc).timestamp()), session=actor,
            payload={'tool': 'get_inventory_risk', 'arguments': args})
        token = wrap_transport(envelope.model_dump_json().encode())['payload']
        header = token+'.'+_mac(token, key)
        self.assertEqual(verify_assertion(header, {'retail': key}, 'get_inventory_risk', args, ReplayGuard()), actor)

    def test_canonical_digest(self):
        self.assertEqual(arguments_digest({'b': 2, 'a': 1}), arguments_digest({'a': 1, 'b': 2}))
        with self.assertRaises(ValueError):
            arguments_digest({'text': 'a'*130000})

    def _graph(self, *, enabled=False, query_result=None, fail=False, ask_tool=True):
        tools = SimpleNamespace(session=session(), container=SimpleNamespace(settings=SimpleNamespace(
            enterprise_ai_nl2sql_enabled=enabled, enterprise_ai_file_search_enabled=True)))
        tools.call = Mock(side_effect=ToolError('private-error') if fail else None,
                          return_value=query_result or {'status': 'completed', 'rows': []})
        responses = Mock()
        answer = {'status': 'completed', 'output': 'Here is the answer', 'tool_calls': []}
        responses.create_response_sync.side_effect = ([{'status': 'completed', 'output': '', 'tool_calls': [
            {'id': 'call1', 'name': 'query_retail_data', 'arguments': '{"dataset":"inventory"}'}]}, answer]
            if ask_tool else [answer])
        result = run_conversation(responses=responses, tools=tools, message='Inventory question')
        return result, responses, tools

    def test_disabled_query_not_advertised_but_named_tools_remain(self):
        _, responses, _ = self._graph(ask_tool=False)
        names = {tool['name'] for tool in responses.create_response_sync.call_args.kwargs['tools']}
        self.assertNotIn('query_retail_data', names)
        self.assertTrue({'get_inventory_risk', 'get_item_sales_history', 'search_policy_documents'} <= names)

    def test_disabled_query_cannot_be_called_by_model(self):
        result, _, tools = self._graph()
        tools.call.assert_not_called()
        self.assertTrue(result['failed_tools'])

    def test_successful_query_is_tracked(self):
        result, _, _ = self._graph(enabled=True)
        self.assertEqual(result['successful_tools'], ['query_retail_data'])
        self.assertEqual(result['failed_tools'], [])

    def test_failed_query_is_not_success(self):
        for options in ({'fail': True}, {'query_result': {'status': 'not_configured', 'rows': []}}):
            with self.subTest(options=options):
                result, _, _ = self._graph(enabled=True, **options)
                self.assertEqual(result['successful_tools'], [])
                self.assertEqual(result['failed_tools'], ['query_retail_data'])

    def test_failed_tool_does_not_get_verified_answer_route(self):
        container = SimpleNamespace(cache=OCICacheClient(allow_memory=True), responses=SimpleNamespace(enabled=True),
            settings=SimpleNamespace(enterprise_ai_conversation_state_enabled=False))
        service = RetailConversationService(container)
        service._load_history = Mock(return_value={'exists': False, 'next_turn': 1, 'messages': []})
        service._save_turn = Mock()
        service._run = Mock(return_value={'answer': 'Invented facts', 'llm_status': 'completed',
            'used_tools': ['query_retail_data'], 'successful_tools': [], 'failed_tools': ['query_retail_data']})
        result = service.respond(SimpleNamespace(conversation_id=None, workflow_thread_id=None, message='Inventory?'),
                                 session=session())
        self.assertEqual(result['response_route'], 'LLM')
        self.assertEqual(service._save_turn.call_args.kwargs['status'], 'FAILED')
        self.assertNotIn('Invented', result['message'])

    def test_enabled_query_executes_fixed_read_operation(self):
        db = Mock()
        db.fetch_all.return_value = [{'item_id': 'ITEM001'}]
        client = EnterpriseAINL2SQLClient(enabled=True, endpoint='configured', database_client=db)
        result = client.query(dataset='inventory', allowed_tables=sorted(POC_NL2SQL_TABLES), limit=3)
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(db.fetch_all.call_args.args[0], 'nl2sql_inventory')
        self.assertEqual(db.fetch_all.call_args.args[1]['page_size'], 3)
        db.execute.assert_not_called()

    def test_query_rejects_unapproved_input(self):
        client = EnterpriseAINL2SQLClient(enabled=True, endpoint='configured', database_client=Mock())
        for value in ({'dataset': 'delete'}, {'dataset': 'inventory', 'limit': 1000},
                      {'dataset': 'inventory', 'item_id': "ITEM001' OR 1=1"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.query(allowed_tables=sorted(POC_NL2SQL_TABLES), **value)

    def test_both_roles_still_manage_registry(self):
        for role in ('retail-manager', 'supplier'):
            with self.subTest(role=role):
                api = FastAPI()
                api.include_router(router)
                api.dependency_overrides[current_session] = lambda: session(role)
                service = Mock()
                service.list_agents.return_value = {'items': [], 'has_more': False}
                service.set_active.side_effect = RegistryConflict()
                api.dependency_overrides[registry_service] = lambda: service
                with TestClient(api) as client:
                    self.assertEqual(client.get('/registry/agents').status_code, 200)
                    self.assertEqual(client.post('/registry/agents/'+str(uuid4())+'/activate').status_code, 409)
                    service.set_active.assert_called_once()

    def test_registry_failure_does_not_leak_provider_text(self):
        def list_agents():
            raise RuntimeError('SECRET-provider-response')
        with self.assertRaises(HTTPException) as result:
            invoke(list_agents)
        self.assertEqual(result.exception.status_code, 503)
        self.assertNotIn('SECRET', result.exception.detail)

    def test_timeout_category_handles_wrapping(self):
        try:
            try:
                raise TimeoutError('private endpoint')
            except TimeoutError as exc:
                raise RuntimeError('secret') from exc
        except RuntimeError as exc:
            self.assertEqual(protocol_details(exc)['reason'], 'timeout')

    def test_logging_redacts_exception_text(self):
        try:
            raise RuntimeError('SECRET-password')
        except RuntimeError:
            import sys
            record = logging.LogRecord('test', logging.ERROR, __file__, 1, 'operation_failed', (), sys.exc_info())
        formatted = JsonFormatter().format(record)
        self.assertNotIn('SECRET', formatted)
        self.assertIn('RuntimeError', formatted)


if __name__ == '__main__':
    unittest.main(verbosity=2)
