"""Supplier-owned read-only chat, screened before inference or business lookups."""
from copy import copy
from datetime import datetime, timezone
import logging
import re
from uuid import uuid4

from app.agents.retail_conversation import run_conversation
from app.agents.tool_access import SupplierToolAccess
from app.agents.topic_scope import check_topic_scope
from app.chat_models import ChatModelProfile
from app.chat_telemetry import ChatTrace, MeasuredResponses, active_trace, event
from app.enterprise_ai.guardrails import GuardrailDenied, local_security_decision
from app.oci.cache import CacheUnavailable
from app.services.cases import CaseService, CaseNotFound
from app.services.agent_registry import enforce_agent
from app.services.delivery_agent import DeliveryAgentService
from app.services.retail_conversation import security_response, is_action_request, ACTION_CLAIM, BLOCKED_MESSAGE
from app.observability import LangfuseObservabilityClient, TraceContext

logger = logging.getLogger(__name__)
DELIVERY_LOOKUP = re.compile(r'\b(distributors?|couriers?|carriers?|logistics|blue\s?dart|delhivery|ekart|dtdc|delivery|deliveries|shipping)\b', re.I)
DELIVERY_HELP = re.compile(r'\b(register|registration|connect|connection|integrat\w*|api|token|guardrails?|architecture|workflow|approval|tools?)\b', re.I)
DELIVERY_SCOPE_MESSAGE = 'I can help with your assigned requests, supplier stock, policy terms and approval workflow. I do not currently have delivery or distributor information to answer that question.'


def is_delivery_lookup(message):
    # Configuration/workflow explanations are still answered by supplier chat.
    return bool(DELIVERY_LOOKUP.search(message) and not DELIVERY_HELP.search(message))


class SupplierConversationService:
    def __init__(self, container):
        self.container = container

    def respond(self, request, *, session):
        CaseService._session(session).require_role('supplier')
        enforce_agent(self.container, 'supplier')
        # Supplier selection stays server-owned; no retail profile or workflow context.
        if request.model_profile or request.use_file_search or request.workflow_thread_id:
            raise ValueError('Supplier chat uses its configured model and assigned request tools')
        settings = self.container.settings
        if settings.remote_mcp_enabled and not settings.enterprise_ai_responses_enabled:
            # The source-build dashboard provisions model/project configuration only on
            # retail and MCP. Delegate the entire guarded turn to MCP, not to retail.
            # That boundary verifies the signed supplier identity and screens the prompt.
            return SupplierToolAccess(self.container, session).call('answer_supplier_question',
                message=request.message, conversation_id=request.conversation_id)
        profile = ChatModelProfile(id='supplier-baseline', label='Supplier model',
            model_id=settings.enterprise_ai_model_id or 'unconfigured')
        trace = ChatTrace(profile)
        token = active_trace.set(trace)
        tracing = getattr(self.container, 'langfuse', None) or LangfuseObservabilityClient()
        try:
            with tracing.observation('supplier.chatbot.request',
                    metadata={'workflow': 'supplier_conversation'},
                    context=TraceContext(session_id=str(session.session_id), role=session.role,
                                         supplier_id=session.supplier_id, agent='supplier')):
                event('agent_mapping', 'local', tool='supplier')
                event('tool_transport', 'remote_mcp' if settings.remote_mcp_enabled else 'local_functions')
                try:
                    if settings.prompt_guardrails_enabled:
                        self.container.guardrails.require_sync(request.message, session.role, stage='input', source='user')
                    result = self._turn(request, session, trace)
                except GuardrailDenied as exc:
                    result = security_response(exc, request)
                event('chat', result['llm_status'])
                result['metrics'] = trace.finish(result['llm_status'])
                return result
        finally:
            active_trace.reset(token)

    def _turn(self, request, session, trace):
        conversation_id = str(request.conversation_id or uuid4())
        key = 'supplier_chat:' + conversation_id
        cache = self.container.cache
        owner = {'session_id': str(session.session_id), 'supplier_id': session.supplier_id}
        with cache.workflow_lock(key):
            stored = cache.get_state(key)
            if (stored and stored.get('owner') != owner) or (request.conversation_id and not stored):
                raise CaseNotFound('Conversation not found or expired')
            history = (stored or {}).get('messages', [])
            response = {'mode': 'deterministic_read_only', 'allowed_actions': ['view_assigned_cases'],
                'conversation_id': conversation_id, 'used_tools': [], 'llm_status': 'completed',
                'guardrail_status': 'PASSED' if self.container.settings.prompt_guardrails_enabled else 'NOT_APPLIED'}
            tools = SupplierToolAccess(self.container, session)
            delivery = DeliveryAgentService(self.container).availability(session=session)
            if re.fullmatch(r'(hi|hello|hey|good\s+(morning|afternoon|evening))[!.\s]*', request.message, re.I):
                response.update(message='Hi! How can I help? We can review your assigned requests, check stock and policy, or explain the supplier workflow.')
            elif is_action_request(request.message):
                response.update(message=BLOCKED_MESSAGE, llm_status='blocked', guardrail_status='BLOCKED',
                    security_details=local_security_decision('action_requires_approval'))
            elif not delivery['available'] and is_delivery_lookup(request.message):
                event('delivery_capability', 'unavailable', tool='ask_delivery_agent')
                response.update(message=DELIVERY_SCOPE_MESSAGE, mode='scope_guidance')
            elif delivery['available'] and is_delivery_lookup(request.message):
                # The current registry/session state is authority. A model must not
                # skip a lookup and substitute a stale capability claim from history.
                try:
                    result = tools.call('ask_delivery_agent', message=request.message)
                except Exception:
                    logger.warning('supplier_delivery_lookup_unavailable')
                    result = {'status': 'unavailable', 'message': 'I could not retrieve delivery information right now. Please try again.'}
                response.update(message=result['message'], used_tools=['ask_delivery_agent'],
                    mode='delivery_answer' if result['status'] == 'completed' else 'deterministic_read_only',
                    llm_status='completed' if result['status'] == 'completed' else 'unavailable')
            elif not re.search(r'\b(delivery|deliveries|distributors?|shipping)\b', request.message, re.I) and re.search(r'\b(requests?|pending|incoming)\b', request.message, re.I) and re.search(
                    r'\b(show|display|list|pending|incoming)\b', request.message, re.I):
                pending = bool(re.search(r'\b(pending|awaiting)\b', request.message, re.I))
                page = tools.call('list_supplier_requests', pending_only=pending, limit=20, offset=0)
                response.update(request_page=page, used_tools=['list_supplier_requests'],
                    message=('Requests awaiting your supplier approval are shown below.' if pending else
                             'Your assigned requests are shown below.') if page['items'] else
                            ('No assigned requests are awaiting your approval.' if pending else 'No assigned requests were found.'))
            elif not self.container.responses.enabled:
                response.update(llm_status='disabled', message='Supplier AI conversation is not configured. You can still list assigned requests and use their review controls.')
            else:
                # Shared native File Search can contain other suppliers' documents.
                # Only the supplier-scoped policy tool is permitted in this conversation.
                client = copy(self.container.responses)
                client.vector_store_ids = []
                measured = MeasuredResponses(client, trace)
                try:
                    result = check_topic_scope(measured, request.message, history)
                    if result is None:
                        result = run_conversation(responses=measured, tools=tools,
                            message=request.message, history=history, input_screened=True,
                            delivery_available=delivery['available'])
                    answer = result['answer']
                    if 'ask_delivery_agent' in result.get('failed_tools', []):
                        answer = 'The delivery lookup did not return verified information. Check that the registered agent is active and its session connection is valid, then retry.'
                        result['llm_status'] = 'unavailable'
                    direct_delivery = bool(result.get('delivery_answers')) and 'ask_delivery_agent' not in result.get('failed_tools', [])
                    if not direct_delivery and ACTION_CLAIM.search(answer):
                        response.update(message='Chat cannot confirm an approval, dispatch or booking. Check the saved request and use its review controls.',
                                        llm_status='blocked', guardrail_status='BLOCKED',
                                        security_details=local_security_decision('unverified_action_claim'))
                    else:
                        response.update(message=answer, llm_status=result['llm_status'],
                                        used_tools=result['used_tools'], mode='delivery_answer' if direct_delivery else 'llm_read_only')
                except Exception:
                    logger.warning('supplier_conversation_unavailable', exc_info=True)
                    response.update(message='Supplier conversation is temporarily unavailable. Your saved requests and approval controls remain available.', llm_status='unavailable')
            # Keep operational errors/scope notices in the UI, not as stale facts
            # for the next model turn. Reconnection never requires a new chat.
            turns = history if response['mode'] == 'scope_guidance' or response['llm_status'] == 'unavailable' else history + [
                {'role': 'user', 'content': request.message}, {'role': 'assistant', 'content': response['message']}]
            turns = turns[-20:]
            while sum(len(t['content']) for t in turns) > 120000:
                turns = turns[2:]
            ttl = max(1, int((session.expires_at - datetime.now(timezone.utc)).total_seconds()))
            if not cache.set_state(key, {'owner': owner, 'messages': turns}, ttl_seconds=ttl):
                raise CacheUnavailable('Supplier chat history could not be stored')
            return response
