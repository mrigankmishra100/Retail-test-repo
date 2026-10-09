"""Session-owned Oracle chat history with bounded read-only AI routing."""
import logging
import re
from time import monotonic
from uuid import uuid4
from app.agents.retail_conversation import run_conversation
from app.agents.tool_access import RetailToolAccess
from app.services.cases import CaseService, CaseNotFound, CaseConflict
from app.oci.cache import CacheUnavailable
from fastmcp.exceptions import ToolError

logger = logging.getLogger(__name__)

ACTION_REQUEST = re.compile(
    r"\b(?:please\s+|can\s+you\s+|could\s+you\s+|go\s+ahead\s+and\s+)?"
    r"(?:approve|reject|dispatch|send|submit|place|create|close|complete|select)\b"
    r".{0,50}\b(?:case|request|response|order|purchase|supplier|it|this|that)\b"
    r"|^\s*(?:please\s+)?(?:approve|reject|dispatch|send|submit|place|create|close|complete|select)\b",
    re.IGNORECASE,
)
ACTION_CLAIM = re.compile(
    r"\b(?:i|we|the\s+(?:assistant|system|agent))\s+(?:have\s+)?"
    r"(?:approved|rejected|dispatched|sent|submitted|placed|created|closed|completed|selected)\b"
    r"|\b(?:case|request|response|order|purchase|supplier)\s+"
    r"(?:has\s+been|was|is\s+now)\s+"
    r"(?:approved|rejected|dispatched|sent|submitted|placed|created|closed|completed|selected)\b",
    re.IGNORECASE,
)
BLOCKED_MESSAGE = (
    "I cannot record approvals, select a supplier, place an order, or dispatch notifications in chat. "
    "Use the dedicated Cases & approvals or Replenishment controls."
)


def is_action_request(message):
    # Informational questions about the process remain answerable; action commands do not.
    if re.match(r"^\s*(?:how|what|why|where|when|explain|describe)\b", message, re.IGNORECASE):
        return False
    return ACTION_REQUEST.search(message) is not None


class RetailConversationService:
    def __init__(self, container):
        self.container = container

    def _run(self, *, session, message, history=(), facts=None, conversation_id=None):
        CaseService._session(session).require_role("retail-manager")
        settings = self.container.settings
        # Enabled hosted protections/continuity must never be silently bypassed.
        if settings.enterprise_ai_guardrails_enabled:
            raise NotImplementedError("Hosted guardrails are not implemented")
        if settings.enterprise_ai_conversation_state_enabled and settings.enterprise_ai_api_mode != 'responses':
            raise RuntimeError('Hosted conversations require OCI Responses mode')
        return run_conversation(responses=self.container.responses,
            tools=RetailToolAccess(self.container, session), message=message, history=history, facts=facts,
            conversation_id=conversation_id)

    def _load_history(self, conversation_id, workflow_id, session):
        started = monotonic()
        try:
            result = RetailToolAccess(self.container, session).call(
                "get_chat_history", conversation_id=conversation_id,
                workflow_thread_id=workflow_id, limit=8)
            logger.info('chat_history_loaded', extra={'conversation_id': conversation_id,
                'status': 'success', 'duration_ms': round((monotonic()-started)*1000, 2)})
            return result
        except (CaseNotFound, CaseConflict):
            raise
        except ToolError as exc:
            logger.warning('chat_history_failed', extra={'conversation_id': conversation_id,
                'stage': 'history_read', 'error_type': type(exc).__name__})
            code = str(exc).split(";", 1)[0]
            if code == "not_found":
                raise CaseNotFound("Conversation not found") from None
            if code == "conflict":
                raise CaseConflict("Keep the conversation's workflow_thread_id unchanged") from None
            raise RuntimeError("Conversation history unavailable") from None

    def _save_turn(self, *, conversation_id, workflow_id, turn_number, request_message,
                   answer, route, status, guardrail, used_tools, llm_status, session,
                   error_code=None):
        try:
            return RetailToolAccess(self.container, session).call(
                "save_chat_turn", conversation_id=conversation_id,
                workflow_thread_id=workflow_id, turn_number=turn_number,
                user_message=request_message, assistant_message=answer,
                response_route=route, response_status=status,
                guardrail_status=guardrail,
                evidence_metadata={"used_tools": used_tools, "llm_status": llm_status},
                error_code=error_code)
        except ToolError as exc:
            logger.warning('chat_history_failed', extra={'conversation_id': conversation_id,
                'stage': 'history_write', 'error_type': type(exc).__name__})
            raise RuntimeError("Conversation history unavailable") from None

    @staticmethod
    def _model_history(history):
        return [{"role": message["role"], "content": message["content"]}
                for message in history["messages"]]

    def explain(self, *, session, facts):
        if not self.container.responses.enabled:
            return {"advisory": None, "llm_status": "disabled"}
        try:
            result = self._run(session=session, facts=facts,
                message=("Write a concise advisory for the manager using plain text only, with no Markdown or headings. "
                         "Use at most three short sentences: explain the inventory risk, identify the leading feasible "
                         "supplier and reason, then state the most important caution. Do not list every supplier or imply "
                         "that any supplier was selected, approved, ordered from, or notified. Use only supplied facts; "
                         "additional tools only if needed."))
            return {"advisory": result["answer"], "llm_status": result["llm_status"]}
        except Exception:
            logger.warning("Retail model explanation unavailable; deterministic workflow retained")
            return {"advisory": None, "llm_status": "unavailable"}

    def respond(self, request, *, session):
        CaseService._session(session).require_role("retail-manager")
        conversation_id = str(request.conversation_id or uuid4())
        workflow_id = str(request.workflow_thread_id) if request.workflow_thread_id else None
        cache, facts = self.container.cache, None
        # Read authoritative workflow/case state on every turn, never trust cached assistant prose.
        if workflow_id:
            view = self.container.retail_workflow_service.get(workflow_id, session=session)
            facts = view.model_dump(mode="json", exclude={"advisory", "available_tools", "message"})
        with cache.workflow_lock("chat:" + conversation_id):
            stored = self._load_history(conversation_id, workflow_id, session)
            if request.conversation_id and not stored["exists"]:
                raise CaseNotFound("Conversation not found or expired")
            history = self._model_history(stored)
            turn_number = stored["next_turn"]
            if is_action_request(request.message):
                result = {"answer": BLOCKED_MESSAGE, "llm_status": "blocked", "used_tools": []}
                route, guardrail, status = "BLOCKED", "BLOCKED", "BLOCKED"
            elif not self.container.responses.enabled:
                result = {"answer": "AI conversation is disabled. Use the deterministic inventory and workflow controls.",
                          "llm_status": "disabled", "used_tools": []}
                route, guardrail, status = "LLM", "NOT_APPLIED", "COMPLETED"
            else:
                hosted = self.container.settings.enterprise_ai_conversation_state_enabled
                provider_key = "retail_conversation_provider:" + conversation_id
                provider = cache.get_state(provider_key) if hosted else None
                if provider:
                    if (provider.get("session_id") != str(session.session_id)
                            or provider.get("workflow_thread_id") != workflow_id):
                        raise CaseNotFound("Conversation not found or expired")
                    if provider.get("in_flight"):
                        raise CaseConflict("Conversation has an incomplete turn; start a new conversation")
                hosted_id, created = None, False
                try:
                    if hosted:
                        if self.container.settings.enterprise_ai_api_mode != "responses":
                            raise RuntimeError("Hosted conversations require OCI Responses mode")
                        hosted_id = (provider or {}).get("oci_conversation_id")
                        if not hosted_id:
                            hosted_id = self.container.conversation.create_sync()
                            created = True
                        provider = {"session_id": str(session.session_id),
                                    "workflow_thread_id": workflow_id,
                                    "oci_conversation_id": hosted_id, "in_flight": True}
                        if not cache.set_state(provider_key, provider):
                            raise CacheUnavailable("Conversation ownership could not be stored")
                    result = self._run(
                        session=session, message=request.message,
                        history=history if not hosted or created else (), facts=facts,
                        conversation_id=hosted_id)
                    CaseService._session(session).require_role("retail-manager")
                    if hosted:
                        provider["in_flight"] = result["llm_status"] != "completed"
                        if not cache.set_state(provider_key, provider):
                            raise CacheUnavailable("Conversation ownership could not be stored")
                except Exception as exc:
                    logger.warning('retail_conversation_failed', extra={'conversation_id': conversation_id,
                        'stage': 'model_or_conversation', 'error_type': type(exc).__name__}, exc_info=True)
                    if created and hosted_id:
                        try:
                            self.container.conversation.delete_sync(hosted_id)
                        except Exception:
                            logger.warning("Temporary OCI conversation cleanup failed; retention policy applies")
                    # Persist a non-sensitive failed turn so audit history is complete.
                    try:
                        self._save_turn(conversation_id=conversation_id, workflow_id=workflow_id,
                            turn_number=turn_number, request_message=request.message,
                            answer="Retail conversation unavailable.", route="LLM", status="FAILED",
                            guardrail="NOT_APPLIED", used_tools=[], llm_status="disabled", session=session,
                            error_code="model_unavailable")
                    except Exception as history_exc:
                        logger.warning('chat_failed_turn_not_saved', extra={'conversation_id': conversation_id,
                            'stage': 'history_write', 'error_type': type(history_exc).__name__})
                    logger.warning("Retail conversation model request unavailable")
                    raise RuntimeError("Retail conversation unavailable") from None
                route = "NL2SQL" if "query_retail_data" in result.get("successful_tools", []) else "LLM"
                # These are local business checks, not a managed OCI guardrail verdict.
                guardrail, status = "NOT_APPLIED", "COMPLETED"
                if result.get('failed_tools') or result['llm_status'] != 'completed':
                    route, status = 'LLM', 'FAILED'
                    if result.get('failed_tools'):
                        result = {**result, 'answer': 'The requested live data could not be verified. Please retry later or use the inventory and workflow controls.',
                                  'llm_status': 'disabled'}
                    logger.warning('retail_conversation_incomplete', extra={'conversation_id': conversation_id,
                        'stage': 'tool_results', 'status': 'failed', 'reason': 'unverified_data_or_limit'})
                if ACTION_CLAIM.search(result["answer"]):
                    result = {"answer": BLOCKED_MESSAGE, "llm_status": "blocked",
                              "used_tools": result["used_tools"]}
                    route, guardrail, status = "BLOCKED", "BLOCKED", "BLOCKED"
            try:
                self._save_turn(conversation_id=conversation_id, workflow_id=workflow_id,
                    turn_number=turn_number, request_message=request.message, answer=result["answer"],
                    route=route, status=status, guardrail=guardrail,
                    used_tools=result["used_tools"], llm_status=result["llm_status"], session=session)
            except RuntimeError:
                logger.warning("Retail conversation history write unavailable")
                raise
        return {"mode": "llm_read_only" if result["llm_status"] == "completed" else "deterministic_read_only",
            "message": result["answer"], "llm_status": result["llm_status"],
            "conversation_id": conversation_id, "workflow_thread_id": workflow_id,
            "used_tools": result["used_tools"], "allowed_actions": ["view_inventory_risks", "view_cases", "start_retail_workflow"],
            "response_route": route, "guardrail_status": guardrail,
            "case": view.case if workflow_id else None,
            "pending_approval": view.case.pending_approval if workflow_id and view.case else None}
