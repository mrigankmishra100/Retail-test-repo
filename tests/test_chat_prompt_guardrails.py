"""Chat input uses OCI detection once, independently of legacy deployment switches."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
from app.config import Settings
from app.contracts import ChatRequest
from app.enterprise_ai.guardrails import EnterpriseAIGuardrailsClient
from app.services.retail_conversation import RetailConversationService
from app.services.chat_experience import ChatExperienceService
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.services.sessions import SessionContext


def actor():
    return SessionContext(session_id=uuid4(), role='retail-manager',
                          expires_at=datetime.now(timezone.utc) + timedelta(hours=1))


def test_dashboard_legacy_false_does_not_override_new_image_prompt_setting(monkeypatch):
    monkeypatch.setenv('CHAT_PROMPT_GUARDRAILS_ENABLED', 'true')
    monkeypatch.setenv('APP_CONFIG_JSON', json.dumps({'ENTERPRISE_AI_GUARDRAILS_ENABLED': False,
        'OCI_AUTH_MODE': 'resource_principal', 'OCI_REGION': 'us-ashburn-1', 'OCI_COMPARTMENT_ID': 'fixture'}))
    settings = Settings(_env_file=None)
    assert settings.prompt_guardrails_enabled
    guard = EnterpriseAIGuardrailsClient.from_settings(settings)
    assert guard.enabled and not guard.custom_rules_enabled
    assert guard.auth_mode == 'resource_principal'
    assert guard.endpoint == 'https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com'
    # An explicit new setting remains authoritative for operators.
    assert not Settings(_env_file=None, chat_prompt_guardrails_enabled=False).prompt_guardrails_enabled


def test_configured_chat_detector_calls_oci_even_for_credential_pattern():
    settings = Settings(_env_file=None, chat_prompt_guardrails_enabled=True,
                        oci_region='us-ashburn-1', oci_compartment_id='fixture')
    guard = EnterpriseAIGuardrailsClient.from_settings(settings)
    guard.client = Mock()
    guard.client.apply_guardrails.return_value = SimpleNamespace(data={
        'results': {'prompt_injection': {'score': 0}}, 'guardrail_version': {'version': '1.0.1'}})
    verdict = guard.evaluate_sync('password=FAKE_DEMO_VALUE', 'retail-manager')
    assert verdict['provider'] == 'oci' and verdict['api_called'] and verdict['status'] == 'PASSED'
    guard.client.apply_guardrails.assert_called_once()


def test_prompt_screening_does_not_recheck_history_tools_or_model_output():
    settings = Settings(_env_file=None, chat_prompt_guardrails_enabled=True,
                        oci_region='us-ashburn-1', oci_compartment_id='fixture')
    container = SimpleNamespace(settings=settings, guardrails=Mock(), responses=Mock(), langfuse=None)
    service = RetailConversationService(container)
    request = ChatRequest(message='Show stock')
    session = actor()
    with patch('app.services.agent_registry.enforce_agent'), patch.object(service, '_respond', return_value={'ok': True}):
        service.respond(request, session=session)
    container.guardrails.require_sync.assert_called_once_with('Show stock', session.role, stage='input', source='user')
    with patch('app.services.retail_conversation.run_conversation', return_value={}) as run:
        service._run(session=session, message='Show stock', history=[{'content': 'history'}])
    assert run.call_args.kwargs['guardrails'] is None
    container.guardrails.require_sync.assert_called_once()


def test_model_comparison_screens_each_prompt_once():
    settings = Settings(_env_file=None, chat_prompt_guardrails_enabled=True,
                        oci_region='us-ashburn-1', oci_compartment_id='fixture')
    service = Mock()
    service.container.settings = settings
    service._run.return_value = {'answer': 'Stock is 100.', 'llm_status': 'completed', 'used_tools': []}
    result = ChatExperienceService._evaluate_checked(service, ChatRequest(message='Show stock'), actor())
    service.container.guardrails.require_sync.assert_called_once()
    assert result['guardrail_status'] == 'PASSED'
