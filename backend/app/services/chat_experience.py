"""Scoped model selection and bounded, read-only comparison runs."""
from copy import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from threading import BoundedSemaphore, Lock
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.chat_models import ChatModelProfile, profiles
from app.external_models import available_profiles
from app.regional_evaluation import RegionalEvaluationModel, REGIONAL_PROFILE
from time import monotonic
import logging
import sqlite3
from app.chat_store import ChatStore
from app.chat_telemetry import ChatTrace, MeasuredResponses, active_trace, event
from app.contracts import ChatRequest
from app.enterprise_ai.conversation import EnterpriseAIConversationClient
from app.services.cases import CaseService, CaseConflict, CaseNotFound
from app.services.retail_conversation import (RetailConversationService, is_action_request,
    has_unverified_action_claim, BLOCKED_MESSAGE)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    model_profiles: list[str] = Field(min_length=2, max_length=3)
    use_file_search: bool = False
    expected_terms: list[str] = Field(default_factory=list, max_length=10)
    regional_model: RegionalEvaluationModel | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.model_profiles)) != len(self.model_profiles):
            raise ValueError('Choose distinct model profiles')
        if (REGIONAL_PROFILE in self.model_profiles) != (self.regional_model is not None):
            raise ValueError('Select and configure the regional evaluation model together')
        if any(not term.strip() or len(term) > 150 for term in self.expected_terms):
            raise ValueError('Expected terms must contain 1–150 characters')
        return self


class ChatCatalog(BaseModel):
    models: list[dict]
    enabled: bool
    default_profile: str | None
    route: str
    tool_execution: str
    nl2sql_enabled: bool
    configured_providers: list[str] = Field(default_factory=list)


class ChatContainerView:
    """Override model adapters only; share the original lazy services and locks."""
    def __init__(self, base):
        self._base = base

    def __getattr__(self, name):
        return getattr(self._base, name)


class ChatExperienceService:
    def __init__(self, container):
        self.container = container
        self.store = ChatStore(container.settings.chat_metrics_path)
        self.slots, self.lock, self.pending = BoundedSemaphore(2), Lock(), set()

    @property
    def remote(self):
        return 'retail' in self.container.settings.agent_endpoints

    def catalog(self, *, session):
        CaseService._session(session).require_role('retail-manager')
        if self.remote:
            return {**self.container.remote_retail_agent.invoke('chat_catalog', {}, session), 'regional_evaluation_enabled': False}
        settings = self.container.settings
        configured = available_profiles(self.container, session)
        return {'models': [{**p.model_dump(), 'file_search': p.file_search and bool(settings.enterprise_ai_vector_store_ids)}
                           for p in configured.values()],
            'configured_providers': ['openai'] if getattr(settings, 'openai_api_key', None) else [],
            'enabled': settings.enterprise_ai_responses_enabled or any(p.provider != 'oci' for p in configured.values()),
            'regional_evaluation_enabled': True,
            'default_profile': next(iter(configured), None), 'route': 'local_retail_agent',
            'tool_execution': 'remote_mcp' if settings.remote_mcp_enabled else 'local_functions',
            'nl2sql_enabled': settings.enterprise_ai_nl2sql_enabled}

    def selection(self, request, session=None):
        configured = available_profiles(self.container, session)
        profile = configured.get(request.model_profile or next(iter(configured), ''))
        if profile is None:
            raise ValueError('Choose a configured OCI Responses model profile')
        rag = request.use_file_search
        if rag is None:
            rag = self.container.settings.enterprise_ai_file_search_enabled and profile.file_search
        if rag and (not profile.file_search or not self.container.settings.enterprise_ai_vector_store_ids):
            raise ValueError('File Search is not configured for this model')
        return profile, rag

    def connect_external(self, config, *, session):
        CaseService._session(session).require_role('retail-manager')
        if self.remote:
            return self.container.remote_retail_agent.invoke('connect_model', {'config': config.wire()}, session)
        from app.enterprise_ai.external import ExternalModelClient
        config = config.resolved(self.container.settings)
        ExternalModelClient(config).validate_connection()
        return self.container.external_models.register(config, session).model_dump(mode='json')

    def discover_external(self, config, *, session):
        CaseService._session(session).require_role('retail-manager')
        if self.remote:
            return self.container.remote_retail_agent.invoke('discover_models', {'config': config.wire()}, session)
        from app.enterprise_ai.external import ExternalModelClient
        config = config.resolved(self.container.settings)
        return {'models': ExternalModelClient(config).available_models()}

    def scoped_container(self, profile, rag, trace, session, regional=None):
        # Fresh model dependencies; never mutate another session's model selection.
        scoped = ChatContainerView(self.container)
        scoped.settings = self.container.settings.model_copy(update={
            'enterprise_ai_model_id': profile.model_id, 'enterprise_ai_file_search_enabled': rag})
        client = copy(self.container.responses)
        client.model_id = profile.model_id
        client.vector_store_ids = list(scoped.settings.enterprise_ai_vector_store_ids) if rag else []
        if profile.provider != 'oci':
            if rag:
                raise ValueError('OCI File Search is unavailable for external models; policy function tools remain available')
            client = self.container.external_models.client(profile.id, session)
            scoped.settings = scoped.settings.model_copy(update={
                'enterprise_ai_responses_enabled': True, 'enterprise_ai_conversation_state_enabled': False,
                'enterprise_ai_file_search_enabled': False, 'enterprise_ai_vector_store_ids': [],
                'enterprise_ai_nl2sql_enabled': False})
        elif regional and regional.api_mode == 'native_chat':
            from app.enterprise_ai.native_evaluation import NativeChatEvaluationClient
            if rag:
                raise ValueError('Native Chat does not support Responses File Search')
            scoped.settings = scoped.settings.model_copy(update={
                'enterprise_ai_conversation_state_enabled': False, 'enterprise_ai_vector_store_ids': [],
                'enterprise_ai_api_mode': 'chat', 'enterprise_ai_endpoint': regional.endpoint,
                'oci_compartment_id': regional.compartment_id})
            client = NativeChatEvaluationClient(regional, config_file=scoped.settings.oci_config_file,
                timeout=scoped.settings.enterprise_ai_timeout_seconds)
        elif regional:
            from app.enterprise_ai.responses import EnterpriseAIResponsesClient
            scoped.settings = scoped.settings.model_copy(update={
                'enterprise_ai_endpoint': regional.endpoint, 'enterprise_ai_project_id': regional.project_id,
                'enterprise_ai_model_id': regional.model_id, 'enterprise_ai_auth_mode': regional.auth_mode,
                'oci_auth_mode': 'api_key', 'oci_config_profile': 'DEFAULT',
                'oci_genai_api_key': regional.api_key, 'enterprise_ai_api_mode': 'responses',
                'enterprise_ai_conversation_state_enabled': False,
                'enterprise_ai_vector_store_ids': list(regional.vector_store_ids) if rag else []})
            # Fresh transport: never reuse a cached client or credentials from the baseline.
            client = EnterpriseAIResponsesClient.from_settings(scoped.settings)
        client.observability = getattr(self.container, "langfuse", None)
        scoped.responses = MeasuredResponses(client, trace)
        # A shallow service copy retains the shared database/approval dependencies,
        # but every narrative call uses this selection, including draft tools.
        from app.services.email_drafts import EmailDraftService
        scoped.case_service = copy(self.container.case_service)
        scoped.case_service.email_drafts = EmailDraftService(self.container.database,
            responses=scoped.responses,
            shared_poc_enabled=scoped.settings.oci_notification_shared_poc_enabled)
        scoped.conversation = EnterpriseAIConversationClient(enabled=scoped.settings.enterprise_ai_conversation_state_enabled,
                                                             responses=scoped.responses)
        scoped.chat_selection = {'profile': profile.id, 'model_id': profile.model_id, 'file_search': rag,
                                 'session_id': str(session.session_id)}
        scoped.retail_conversation_service = RetailConversationService(scoped)
        return scoped

    def respond(self, request, *, session, evaluation=False, expected_terms=(), regional=None):
        CaseService._session(session).require_role('retail-manager')
        if regional and (not evaluation or self.remote or request.model_profile != REGIONAL_PROFILE):
            raise ValueError('Regional configuration is supported only in local read-only evaluations')
        if self.remote:
            # Keep the catalog and allowlist authoritative on the agent that runs the model.
            fallback = ChatModelProfile(id=request.model_profile or 'baseline', label='Remote model', model_id='remote-unknown')
            mapping_trace = ChatTrace(profiles(self.container.settings).get(request.model_profile, fallback))
            token = active_trace.set(mapping_trace)
            try:
                op = 'chat_evaluate' if evaluation else 'chat'
                result = self.container.remote_retail_agent.invoke(op, {'request': request.model_dump(mode='json')}, session)
            except Exception:
                event('agent_transport', 'failed')
                self._save(session, {'message': 'Remote agent unavailable; check its configuration and access.',
                    'metrics': mapping_trace.finish('failed', route='remote_retail_agent')}, evaluation)
                raise
            finally:
                active_trace.reset(token)
            if result.get('metrics'):
                result['metrics']['route'] = 'remote_retail_agent'
                result['metrics']['duration_ms'] = round((monotonic()-mapping_trace.started)*1000, 2)
                result['metrics']['events'] = mapping_trace.events + result['metrics']['events']
                self._quality(result, expected_terms)
                self._save(session, result, evaluation)
            return result
        profile, rag = (regional.profile(), bool(request.use_file_search)) if regional else self.selection(request, session)
        if regional and rag and not regional.vector_store_ids:
            raise ValueError('Regional File Search requires a vector store in the target project')
        trace = ChatTrace(profile)
        token = active_trace.set(trace)
        try:
            scoped = self.scoped_container(profile, rag, trace, session, regional=regional)
            event('agent_mapping', 'local', tool='retail')
            event('tool_transport', 'remote_mcp' if scoped.settings.remote_mcp_enabled else 'local_functions')
            event('retrieval', 'enabled' if rag else 'disabled', tool='file_search')
            service = RetailConversationService(scoped)
            result = self._evaluate_one(service, request, session) if evaluation else service.respond(request, session=session)
            status = result['llm_status']
            event('chat', status)
            result['metrics'] = trace.finish(status)
            if regional:
                result['metrics']['region'] = regional.region
                result['metrics']['api_mode'] = regional.api_mode
            self._quality(result, expected_terms)
            self._save(session, result, evaluation)
            return result
        except Exception as exc:
            event('chat', 'failed')
            result = {'message': 'Run unavailable. Check model access, configuration and application events.',
                      'metrics': trace.finish('failed')}
            if regional:
                result['metrics'].update(region=regional.region, api_mode=regional.api_mode)
            self._save(session, result, evaluation)
            exc.chat_metrics = result['metrics']
            raise
        finally:
            active_trace.reset(token)

    @staticmethod
    def _quality(result, terms):
        metrics, text = result['metrics'], result.get('message', '')
        metrics['quality'] = {'nonempty_answer': bool(text.strip()),
            'tools_succeeded': all(e['status'] == 'success' for e in metrics['events'] if e['stage'] == 'function_tool')
                               if any(e['stage'] == 'function_tool' for e in metrics['events']) else None,
            'has_citations': bool(metrics['citations']), 'action_blocked': result.get('llm_status') == 'blocked',
            'expected_terms': [{'term': t, 'found': t.casefold() in text.casefold()} for t in terms],
            'human_rating': None,
            'method': 'Observable checks and optional human rating; no automated factual correctness score'}

    def _save(self, session, result, evaluation):
        try:
            self.store.save(session.session_id, result['metrics']['run_id'], 'turn',
                            {'message': result['message'], 'evaluation': evaluation, 'metrics': result['metrics']})
        except (OSError, sqlite3.Error):
            # A completed chat turn must not be replayed because telemetry storage failed.
            result['metrics']['persistence'] = 'unavailable'
            logging.getLogger(__name__).warning('Chat telemetry persistence unavailable',
                extra={'trace_id': result['metrics']['run_id'], 'stage': 'metrics_write', 'status': 'failed'})

    @staticmethod
    def _evaluate_one(service, request, session):
        from app.services.agent_registry import enforce_agent
        from app.enterprise_ai.guardrails import GuardrailDenied
        from app.services.retail_conversation import security_response
        enforce_agent(service.container, 'retail')
        try:
            return ChatExperienceService._evaluate_checked(service, request, session)
        except GuardrailDenied as exc:
            return security_response(exc, request)

    @staticmethod
    def _evaluate_checked(service, request, session):
        from app.enterprise_ai.guardrails import local_security_decision
        if service.container.settings.prompt_guardrails_enabled:
            service.container.guardrails.require_sync(request.message, session.role, stage='input', source='user')
        if is_action_request(request.message):
            run = {'answer': BLOCKED_MESSAGE, 'llm_status': 'blocked', 'used_tools': [],
                   'security_details': local_security_decision('action_requires_approval')}
        else:
            run = service._run(session=session, message=request.message, check_scope=True)
            if run.get('failed_tools'):
                run = {**run, 'answer': 'The requested live data could not be verified.', 'llm_status': 'unavailable'}
            if has_unverified_action_claim(run['answer']):
                run = {**run, 'answer': BLOCKED_MESSAGE, 'llm_status': 'blocked',
                       'security_details': local_security_decision('unverified_action_claim', source='model')}
        return {'message': run['answer'], 'llm_status': run['llm_status'], 'used_tools': run['used_tools'],
                'mode': 'llm_read_only' if run['llm_status'] == 'completed' else 'deterministic_read_only',
                'allowed_actions': [],
                'security_details': run.get('security_details'),
                'guardrail_status': ('BLOCKED' if run['llm_status'] == 'blocked' else
                                    'PASSED' if service.container.settings.prompt_guardrails_enabled else 'NOT_APPLIED')}

    def reserve_evaluation(self, request, *, session):
        catalog = self.catalog(session=session)
        allowed = {p['id']: p for p in catalog['models']}
        if request.regional_model:
            if self.remote:
                raise ValueError('Regional evaluations require the local retail execution route')
            allowed[REGIONAL_PROFILE] = request.regional_model.profile().model_dump()
        if not catalog['enabled']:
            raise ValueError('Configure an OCI model or connect an external model before comparing')
        if any(p not in allowed for p in request.model_profiles):
            raise ValueError('Choose model profiles available in this session')
        if request.use_file_search and any(not allowed[p]['file_search'] for p in request.model_profiles):
            raise ValueError('Every compared model must support configured File Search')
        owner = str(session.session_id)
        with self.lock:
            if owner in self.pending or not self.slots.acquire(blocking=False):
                raise CaseConflict('A comparison is already running or capacity is full')
            self.pending.add(owner)
        identifier = str(uuid4())
        try:
            self.store.save(owner, identifier, 'comparison', {'status': 'queued', 'message': request.message,
                'regional_model': request.regional_model.public_details() if request.regional_model else None,
                'model_profiles': request.model_profiles, 'use_file_search': request.use_file_search, 'results': []})
        except Exception:
            self.release(owner)
            raise
        return {'run_id': identifier, 'status': 'queued'}

    def release(self, owner):
        with self.lock:
            self.pending.discard(owner)
            self.slots.release()

    def evaluate(self, identifier, request, session):
        owner, results = str(session.session_id), {}
        def save(status):
            self.store.save(owner, identifier, 'comparison', {'status': status, 'message': request.message,
                'regional_model': request.regional_model.public_details() if request.regional_model else None,
                'model_profiles': request.model_profiles, 'use_file_search': request.use_file_search,
                'results': [results[p] for p in request.model_profiles if p in results]})

        def run(profile):
            try:
                result = self.respond(ChatRequest(message=request.message, intent='conversation', model_profile=profile,
                    use_file_search=request.use_file_search), session=session, evaluation=True, expected_terms=request.expected_terms,
                    **({'regional': request.regional_model} if profile == REGIONAL_PROFILE else {}))
            except Exception as exc:
                # Never return provider errors, prompts, SQL or credentials as logs.
                result = {'message': 'Model run failed. Check access and configuration.', 'llm_status': 'failed', 'error': True}
                if getattr(exc, 'public_code', None) == 'native_tools_require_responses':
                    result['message'] = 'This model cannot use function tools with reasoning through native Chat. Choose Responses API and its Responses model ID.'
                if getattr(exc, 'chat_metrics', None):
                    result['metrics'] = exc.chat_metrics
            return {**result, 'profile_id': profile}

        try:
            save('running')
            # At most three read-only runs per comparison; the existing two-job
            # capacity limit still applies. Each worker gets independent context
            # variables, model selection and trace. Works for local and hosted calls.
            with ThreadPoolExecutor(max_workers=len(request.model_profiles), thread_name_prefix='model-comparison') as executor:
                futures = {executor.submit(copy_context().run, run, profile): profile for profile in request.model_profiles}
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
                    save('running')
            save('completed')
        finally:
            self.release(owner)

    def get_evaluation(self, identifier, *, session):
        CaseService._session(session).require_role('retail-manager')
        result = self.store.get(session.session_id, identifier)
        if not result or result['kind'] != 'comparison':
            raise CaseNotFound('Comparison not found')
        with self.lock:
            if result['status'] in {'queued', 'running'} and str(session.session_id) not in self.pending:
                result['status'] = 'interrupted'
        return result

    def metrics(self, *, session):
        CaseService._session(session).require_role('retail-manager')
        return {'records': self.store.list(session.session_id), 'retention': 'Latest 100 records, up to 7 days, for this session'}
