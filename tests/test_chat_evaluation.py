"""Offline regressions for model isolation, usage, ownership and real graph routing."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.chat_api import router
from app.chat_models import ChatModelProfile
from app.chat_telemetry import ChatTrace, active_trace, normalize_usage
from app.chat_store import ChatStore
from app.config import Settings
from app.contracts import ChatRequest
from app.dependencies import ApplicationContainer
from app.enterprise_ai.responses import EnterpriseAIResponsesClient
from app.services.chat_experience import ChatExperienceService, EvaluationRequest
from app.services.retail_conversation import RetailConversationService
from app.services.sessions import SessionContext
from app.services.cases import CaseConflict
from app.session_api import retail_session


def session():
    return SessionContext(session_id=uuid4(), role='retail-manager', expires_at=datetime.now(timezone.utc)+timedelta(hours=1))


class ChatEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.actor = session()
        self.settings = Settings(_env_file=None, enterprise_ai_model_id='baseline-model',
            chat_metrics_path=Path(self.temp.name)/'metrics.db',
            chat_model_profiles=[ChatModelProfile(id='a', label='Model A', model_id='model-a', file_search=True),
                                 ChatModelProfile(id='b', label='Model B', model_id='model-b')])
        self.container = ApplicationContainer(self.settings)
        self.service = self.container.chat_experience_service

    def enable(self):
        self.settings.enterprise_ai_responses_enabled = True
        self.container.responses.enabled = True

    def test_model_client_isolation_and_allowlist(self):
        original = self.container.responses
        p, rag = self.service.selection(ChatRequest(message='Question', model_profile='b'))
        scoped = self.service.scoped_container(p, rag, ChatTrace(p), self.actor)
        self.assertEqual(scoped.responses.model_id, 'model-b')
        self.assertEqual(original.model_id, 'baseline-model')
        self.assertIsNot(scoped.settings, self.settings)
        self.assertEqual(self.settings.enterprise_ai_model_id, 'baseline-model')
        self.assertIs(scoped.cache, self.container.cache)
        self.assertIsNot(scoped.case_service, self.container.case_service)
        self.assertIs(scoped.case_service.database, self.container.case_service.database)
        self.assertIs(scoped.case_service.email_drafts.narratives.responses, scoped.responses)
        with self.assertRaises(ValueError):
            self.service.selection(ChatRequest(message='Question', model_profile='external-model'))
        with self.assertRaises(ValueError):
            self.service.selection(ChatRequest(message='Question', model_profile='b', use_file_search=True))

    def test_usage_cost_includes_cache_without_double_counting_reasoning(self):
        p = ChatModelProfile(id='p', label='P', model_id='model', input_usd_per_million=2,
            output_usd_per_million=8, cached_input_usd_per_million=1, file_search_usd_per_call=.001,
            price_version='Test rates only')
        trace = ChatTrace(p)
        trace.record({'usage': {'input_tokens': 1000, 'output_tokens': 100,
            'input_tokens_details': {'cached_tokens': 200}, 'output_tokens_details': {'reasoning_tokens': 40}},
            'builtin_tools': [{'type': 'file_search_call', 'status': 'completed'}], 'status': 'completed'}, 100)
        self.assertAlmostEqual(trace.finish('completed')['cost_usd'], .0036)
        self.assertEqual(trace.finish('completed')['output_tokens'], 100)
        self.assertIsNone(ChatTrace(self.settings.chat_model_profiles[0]).cost(normalize_usage({}), 0))

    def test_native_file_search_usage_and_citations_preserved(self):
        client = EnterpriseAIResponsesClient(enabled=True, endpoint='https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1',
            model_id='model-a', project_id='ocid1.generativeaiproject.oc1.ap-hyderabad-1.example', vector_store_ids=['vs_test'])
        client.transport = Mock()
        client.transport.request.return_value = {'status': 'completed', 'usage': {'input_tokens': 20, 'output_tokens': 10},
            'output': [{'type': 'file_search_call', 'status': 'completed'}, {'type': 'message', 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': 'Policy says 10 days.', 'annotations': [{'type': 'file_citation', 'filename': 'policy.pdf', 'file_id': 'file1', 'index': 0}]}]}]}
        result = client.create_response_sync([{'role': 'user', 'content': 'What is the policy?'}])
        self.assertEqual(result['usage']['input_tokens'], 20)
        self.assertEqual(result['citations'][0]['filename'], 'policy.pdf')
        self.assertEqual(result['builtin_tools'][0]['type'], 'file_search_call')
        self.assertEqual(client.transport.request.call_args.args[2]['tools'][0]['type'], 'file_search')
        client.create_response_sync([{'role': 'user', 'content': 'Classify this request'}], allow_tools=False,
                                    tools=[{'name': 'ignored', 'parameters': {}}])
        payload = client.transport.request.call_args.args[2]
        self.assertNotIn('tools', payload)
        self.assertNotIn('conversation', payload)

    def test_real_graph_routes_tools_and_sums_multiple_model_calls(self):
        self.enable()
        self.container.responses.create_response_sync = Mock(side_effect=[
            {'status': 'completed', 'output': 'IN_SCOPE', 'tool_calls': [], 'usage': {'input_tokens': 10, 'output_tokens': 2}},
            {'status': 'completed', 'output': '', 'usage': {'input_tokens': 100, 'output_tokens': 10},
             'tool_calls': [{'id': 'c1', 'name': 'get_inventory_risk', 'arguments': '{}'}]},
            {'status': 'completed', 'output': 'Review stock coverage.', 'usage': {'input_tokens': 150, 'output_tokens': 20}, 'tool_calls': []}])
        with patch('app.agents.tool_access.RetailToolAccess.call', return_value={'items': []}) as call:
            result = self.service.respond(ChatRequest(message='Which items are at risk?', model_profile='b'), session=self.actor, evaluation=True, expected_terms=['coverage'])
        call.assert_called_once_with('get_inventory_risk', days=None)
        self.assertEqual(result['metrics']['input_tokens'], 260)
        self.assertEqual(result['metrics']['output_tokens'], 32)
        self.assertTrue(result['metrics']['quality']['expected_terms'][0]['found'])
        self.assertTrue(result['metrics']['quality']['tools_succeeded'])
        self.assertTrue(any(e.get('tool') == 'get_inventory_risk' for e in result['metrics']['events']))
        self.assertIsNone(active_trace.get())

    def test_comparisons_do_not_write_chat_history_or_execute_actions(self):
        self.enable()
        with patch('app.agents.tool_access.RetailToolAccess.call') as tool:
            result = self.service.respond(ChatRequest(message='Please approve this order', model_profile='a'), session=self.actor, evaluation=True)
        tool.assert_not_called()
        self.assertEqual(result['llm_status'], 'blocked')
        self.assertEqual(result['metrics']['call_count'], 0)

    def test_function_registry_rejects_mutation_requested_by_model(self):
        self.enable()
        self.container.responses.create_response_sync = Mock(side_effect=[
            {'status': 'completed', 'output': 'IN_SCOPE', 'tool_calls': []},
            {'status': 'completed', 'tool_calls': [{'id': 'x', 'name': 'send_supplier_request', 'arguments': '{}'}]},
            {'status': 'completed', 'output': 'Done', 'tool_calls': []}])
        with patch('app.agents.tool_access.RetailToolAccess.call') as tool:
            result = self.service.respond(ChatRequest(message='Which item needs review?', model_profile='a'), session=self.actor, evaluation=True)
        tool.assert_not_called()
        self.assertFalse(result['metrics']['quality']['tools_succeeded'])
        self.assertNotEqual(result['llm_status'], 'completed')

    def test_owner_scoped_records_and_ratings(self):
        result = self.service.respond(ChatRequest(message='Approve this order'), session=self.actor, evaluation=True)
        identifier = result['metrics']['run_id']
        self.assertEqual(self.service.store.list(session().session_id), [])
        with self.assertRaises(LookupError):
            self.service.store.rate(session().session_id, identifier, 5)
        self.service.store.rate(self.actor.session_id, identifier, 4)
        self.assertEqual(self.service.store.get(self.actor.session_id, identifier)['metrics']['quality']['human_rating'], 4)

    def test_conversation_requires_same_profile(self):
        p = self.settings.chat_model_profiles[0]
        scoped = self.service.scoped_container(p, False, ChatTrace(p), self.actor)
        service = RetailConversationService(scoped)
        service._load_history = Mock(return_value={'exists': True, 'next_turn': 2, 'messages': []})
        service._save_turn = Mock()
        identifier = uuid4()
        scoped.cache.set_state('chat_model:'+str(identifier), {**scoped.chat_selection, 'profile': 'b'})
        with self.assertRaises(CaseConflict):
            service.respond(ChatRequest(message='Question', intent='conversation', conversation_id=identifier), session=self.actor)
        service._save_turn.assert_not_called()

    def test_selection_binding_survives_fresh_scoped_views(self):
        p = self.settings.chat_model_profiles[0]
        first = self.service.scoped_container(p, False, ChatTrace(p), self.actor)
        later = self.service.scoped_container(p, False, ChatTrace(p), self.actor)
        identifier = str(uuid4())
        first.cache.set_state('chat_model:'+identifier, first.chat_selection)
        self.assertEqual(later.cache.get_state('chat_model:'+identifier), later.chat_selection)

    def test_retrieval_toggle_keeps_conversation_and_history_in_both_directions(self):
        self.enable()
        p = self.settings.chat_model_profiles[0]
        identifier = uuid4()
        initial = self.service.scoped_container(p, False, ChatTrace(p), self.actor)
        initial.cache.set_state('chat_model:'+str(identifier), initial.chat_selection)
        history = [{'role': 'user', 'content': 'Compare supplier policies.'},
                   {'role': 'assistant', 'content': 'Earlier policy comparison.'}]
        for rag in (True, False):
            with self.subTest(file_search=rag):
                scoped = self.service.scoped_container(p, rag, ChatTrace(p), self.actor)
                service = RetailConversationService(scoped)
                service._load_history = Mock(return_value={'exists': True, 'next_turn': 2})
                service._model_history = Mock(return_value=history)
                service._run = Mock(return_value={'answer': 'New comparison.', 'llm_status': 'completed', 'used_tools': []})
                service._save_turn = Mock()
                result = service.respond(ChatRequest(message='Compare again.', intent='conversation',
                    conversation_id=identifier, model_profile='a', use_file_search=rag), session=self.actor)
                self.assertEqual(result['conversation_id'], str(identifier))
                self.assertEqual(service._run.call_args.kwargs['history'], history)
                self.assertEqual(scoped.cache.get_state('chat_model:'+str(identifier))['file_search'], rag)
                service._save_turn.assert_called_once()

    def test_jobs_complete_despite_one_model_failure_and_release_capacity(self):
        self.enable()
        req = EvaluationRequest(message='Question', model_profiles=['a', 'b'])
        reserved = self.service.reserve_evaluation(req, session=self.actor)
        with self.assertRaises(CaseConflict):
            self.service.reserve_evaluation(req, session=self.actor)
        with patch.object(self.service, 'respond', side_effect=[RuntimeError('secret'), {'message': 'Answer'}]):
            self.service.evaluate(reserved['run_id'], req, self.actor)
        result = self.service.get_evaluation(reserved['run_id'], session=self.actor)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['results']), 2)
        self.assertNotIn('secret', json.dumps(result))
        self.assertFalse(self.service.pending)

    def test_api_supplier_denied_and_comparison_owner_checked(self):
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[retail_session] = lambda: self.actor
        with patch('app.chat_api.get_container', return_value=self.container), TestClient(app) as client:
            self.assertEqual(client.get('/chat/models').status_code, 200)
            self.assertEqual(client.get('/chat/evaluations/'+str(uuid4())).status_code, 404)
            self.assertEqual(client.post('/chat/runs/'+str(uuid4())+'/rating', json={'rating': 6}).status_code, 422)
            self.assertEqual(client.post('/chat/evaluations', json={'message': 'Question', 'model_profiles': ['a', 'a']}).status_code, 422)
        app.dependency_overrides.clear()
        with TestClient(app) as client:
            self.assertEqual(client.get('/chat/models').status_code, 401)

    def test_remote_catalog_and_selection_forwarded(self):
        self.settings.agent_endpoints = {'retail': 'unused in test'}
        self.container.remote_retail_agent = Mock()
        self.container.remote_retail_agent.invoke.return_value = {'models': [], 'enabled': True}
        self.assertEqual(self.service.catalog(session=self.actor)['models'], [])
        req = ChatRequest(message='Question', model_profile='b', use_file_search=False)
        self.container.remote_retail_agent.invoke.return_value = {'message': 'Answer'}
        self.service.respond(req, session=self.actor)
        self.assertEqual(self.container.remote_retail_agent.invoke.call_args.args[1]['request']['model_profile'], 'b')

    def test_registry_mapping_reports_actual_endpoint_source(self):
        self.settings.agent_endpoints = {'retail': 'configured'}
        self.container.agent_registry_service = Mock()
        self.container.agent_registry_service.active_endpoint.return_value = 'registered'
        trace = ChatTrace(self.settings.chat_model_profiles[0]); token = active_trace.set(trace)
        try:
            self.assertEqual(self.container._resolve_agent_endpoint('retail'), 'registered')
            self.assertEqual(trace.events[0]['status'], 'registry')
        finally:
            active_trace.reset(token)

    def test_api_background_comparison_and_owner_isolation(self):
        self.enable()
        self.container.responses.create_response_sync = Mock(side_effect=lambda messages, **options: {
            'status': 'completed', 'output': 'IN_SCOPE' if options.get('allow_tools') is False else 'Review coverage.',
            'tool_calls': [], 'usage': {'input_tokens': 100, 'output_tokens': 20}})
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[retail_session] = lambda: self.actor
        with patch('app.chat_api.get_container', return_value=self.container), TestClient(app) as client:
            response = client.post('/chat/evaluations', json={'message': 'Explain coverage', 'model_profiles': ['a', 'b'], 'expected_terms': ['coverage']})
            self.assertEqual(response.status_code, 202)
            identifier = response.json()['run_id']
            result = client.get('/chat/evaluations/'+identifier).json()
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(len(result['results']), 2)
            self.assertEqual(result['results'][1]['metrics']['profile_id'], 'b')
            self.assertEqual(self.container.responses.model_id, 'baseline-model')
            app.dependency_overrides[retail_session] = session
            self.assertEqual(client.get('/chat/evaluations/'+identifier).status_code, 404)
            self.assertEqual(client.get('/chat/metrics').json()['records'], [])

    def test_provider_failure_retains_usage_and_records_no_private_error(self):
        self.enable()
        self.container.responses.project_id = 'ocid1.generativeaiproject.oc1.test.example'
        self.container.responses.endpoint = 'https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1'
        self.container.responses.transport = Mock()
        self.container.responses.transport.request.return_value = {'status': 'incomplete', 'output': [],
            'usage': {'input_tokens': 10, 'output_tokens': 2}}
        with self.assertRaises(Exception):
            self.service.respond(ChatRequest(message='Explain coverage', model_profile='a'), session=self.actor, evaluation=True)
        metrics = self.service.store.list(self.actor.session_id)[0]['metrics']
        self.assertEqual(metrics['status'], 'failed')
        self.assertEqual(metrics['input_tokens'], 10)
        self.assertIsNone(active_trace.get())

    def test_restart_marks_unfinished_job_interrupted(self):
        self.enable()
        req = EvaluationRequest(message='Question', model_profiles=['a', 'b'])
        identifier = self.service.reserve_evaluation(req, session=self.actor)['run_id']
        restarted = ChatExperienceService(self.container)
        self.assertEqual(restarted.get_evaluation(identifier, session=self.actor)['status'], 'interrupted')
        self.service.release(str(self.actor.session_id))

    def test_telemetry_storage_failure_does_not_replay_completed_turn(self):
        with patch.object(self.service.store, 'save', side_effect=OSError('read-only disk')):
            result = self.service.respond(ChatRequest(message='Approve this order'), session=self.actor, evaluation=True)
        self.assertEqual(result['llm_status'], 'blocked')
        self.assertEqual(result['metrics']['persistence'], 'unavailable')

    def test_gpt_oss_replays_minimal_tool_items_and_gemini_keeps_signatures(self):
        reasoning = {'type': 'reasoning', 'id': 'r1', 'content': [{'type': 'reasoning_text', 'text': 'private'}],
                     'summary': [], 'status': 'completed', 'encrypted_content': 'opaque-signature'}
        function = {'type': 'function_call', 'id': 'f1', 'call_id': 'c1', 'name': 'get_number',
                    'arguments': '{}', 'status': 'completed'}
        reply = {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Checking.'}]}
        for model in ('openai.gpt-oss-120b', 'google.gemini-2.5-flash'):
            client = EnterpriseAIResponsesClient(enabled=True, model_id=model,
                endpoint='https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/openai/v1',
                project_id='ocid1.generativeaiproject.oc1.ap-hyderabad-1.example')
            client.transport = Mock()
            client.transport.request.return_value = {'status': 'completed', 'output': [reply]}
            client.create_response_sync([{'role': 'user', 'content': 'Get number'},
                {'role': 'assistant', 'response_items': [reasoning, function, reply]},
                {'role': 'tool', 'tool_call_id': 'c1', 'content': '{"number":42}'}])
            payload = client.transport.request.call_args.args[2]['input']
            if model.startswith('openai'):
                self.assertNotIn('private', json.dumps(payload))
                self.assertEqual(payload[1], {'type': 'function_call', 'call_id': 'c1', 'name': 'get_number', 'arguments': '{}'})
                self.assertEqual(payload[2], {'role': 'assistant', 'content': 'Checking.'})
            else:
                self.assertEqual(payload[1], reasoning)
            self.assertEqual(payload[-1]['call_id'], 'c1')
        self.assertIn('content', reasoning)
        self.assertIn('status', function)

    def test_failed_comparison_keeps_actual_usage_and_safe_error_code(self):
        from app.enterprise_ai.responses_transport import OCIResponsesUnavailable
        self.enable()
        error = OCIResponsesUnavailable('Private provider body must not escape', code='file_search_failed', http_status=404)
        error.chat_result = {'usage': {'input_tokens': 50, 'output_tokens': 3}}
        self.container.responses.create_response_sync = Mock(side_effect=error)
        request = EvaluationRequest(message='Question', model_profiles=['a', 'b'])
        identifier = self.service.reserve_evaluation(request, session=self.actor)['run_id']
        self.service.evaluate(identifier, request, self.actor)
        result = self.service.get_evaluation(identifier, session=self.actor)
        for row in result['results']:
            self.assertEqual(row['metrics']['input_tokens'], 50)
            self.assertEqual(row['metrics']['calls'][0]['error_code'], 'file_search_failed')
            self.assertNotIn('Private provider body', json.dumps(row))


if __name__ == '__main__':
    unittest.main()
