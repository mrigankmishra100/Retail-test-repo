"""Authenticated retail graph lifecycle. Cache coordinates runs; Oracle owns approvals."""
from uuid import uuid5
from langgraph.types import Command
from langsmith import tracing_context
from app.agents.checkpoints import CacheCheckpointer
from app.agents.retail_graph import build_retail_graph, validate_selection
from app.agents.tool_access import RetailToolAccess, RETAIL_TOOL_NAMES
from app.agents.contracts import StartRetailWorkflow, RetailSelection, RetailWorkflowResponse
from app.services.cases import CaseService, CaseNotFound, CaseConflict, digest


class RetailWorkflowService:
    def __init__(self, container):
        self.container, self.cache = container, container.cache

    @staticmethod
    def _session(session):
        CaseService._session(session).require_role("retail-manager")

    @staticmethod
    def _key(thread_id):
        return "retail_owner:" + str(thread_id)

    def _owned(self, thread_id, session):
        self._session(session)
        owner = self.cache.get_state(self._key(thread_id))
        if not owner or owner.get("session_id") != str(session.session_id):
            raise CaseNotFound("Workflow not found or expired")
        return owner

    def _graph(self, session):
        return build_retail_graph(tools=RetailToolAccess(self.container, session),
            cases=self.container.case_service, suppliers=self.container.supplier_service,
            checkpointer=CacheCheckpointer(self.cache),
            conversation=getattr(self.container, "retail_conversation_service", None))

    @staticmethod
    def _config(thread_id):
        return {"configurable": {"thread_id": str(thread_id)}, "recursion_limit": 30}

    def _run(self, graph, command, config):
        # Avoid exporting full commercial checkpoint state via automatic LangGraph tracing.
        # Local request/tool/node trace metadata remains active; hosted telemetry is Phase 11.
        with tracing_context(enabled=False):
            graph.invoke(command, config, durability="sync")

    def _view(self, graph, config, session):
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise CaseNotFound("Workflow checkpoint not found or expired")
        state = snapshot.values
        pending = [i.value for task in snapshot.tasks for i in task.interrupts]
        case = self.container.case_service.get_case(state["case_id"], session=session) if state.get("case_id") else None
        # The retail graph may have ended after request dispatch. Always project subsequent
        # supplier progress from Oracle, never a stale terminal cache status.
        status, message = state["status"], state.get("message", "Workflow in progress; resume retries the pending step.")
        if case:
            updates = {"SUPPLIER_REJECTED": ("supplier_rejected", "Supplier rejected the request; review the case."),
                "RESPONSE_QUEUED": ("response_queued", "Supplier approved; response notification is queued, not yet acknowledged."),
                "SUPPLIER_RESPONDED": ("supplier_responded", "Supplier response notification acknowledged; completion is pending."),
                "COMPLETED": ("completed", "Case completed and approved summary persisted.")}
            status, message = updates.get(case["status"], (status, message))
        return RetailWorkflowResponse(thread_id=config["configurable"]["thread_id"], status=status,
            pending_input=pending[0]["kind"] if pending else None, can_resume=bool(snapshot.next),
            message=message,
            error_code=state.get("error_code") or ("node_failed" if any(t.error for t in snapshot.tasks) else None),
            available_tools=sorted(RETAIL_TOOL_NAMES), inventory=state.get("inventory"),
            comparison=state.get("comparison"), case=case,
            advisory=state.get("advisory"), llm_status=state.get("llm_status", "disabled"))

    def start(self, request, *, session, key):
        self._session(session)
        request = StartRetailWorkflow.model_validate(request).model_dump(mode="json")
        CaseService._request(session, "retail-workflow", request, key)
        thread_id = str(uuid5(session.session_id, "retail-workflow:" + key))
        config = self._config(thread_id)
        with self.cache.workflow_lock(thread_id):
            owner = self.cache.get_state(self._key(thread_id))
            graph = self._graph(session)
            if owner:
                self._owned(thread_id, session)
                if owner["input_hash"] != digest(request):
                    raise CaseConflict("Workflow key already used with different input")
                if graph.get_state(config).values:
                    return self._view(graph, config, session)
            else:
                if graph.get_state(config).values:
                    raise CaseConflict("Checkpoint ownership expired; start with a new key")
                owner = {"session_id": str(session.session_id), "input_hash": digest(request)}
                self.cache.set_state(self._key(thread_id), owner)
            self._run(graph, request | {"thread_id": thread_id, "status": "starting"}, config)
            self.cache.set_state(self._key(thread_id), owner)
            return self._view(graph, config, session)

    def get(self, thread_id, *, session):
        self._owned(thread_id, session)
        with self.cache.workflow_lock(str(thread_id)):
            return self._view(self._graph(session), self._config(thread_id), session)

    def select(self, thread_id, selection, *, session):
        self._session(session)
        selection = RetailSelection.model_validate(selection).model_dump(mode="json")
        with self.cache.workflow_lock(str(thread_id)):
            owner = self._owned(thread_id, session)
            graph, config = self._graph(session), self._config(thread_id)
            view = self._view(graph, config, session)
            if owner.get("selection") and owner["selection"] != selection:
                raise CaseConflict("A different selection was already submitted; start a new workflow")
            if view.pending_input != "selection":
                if owner.get("selection") == selection:
                    return view
                raise CaseConflict("Workflow is not awaiting supplier selection")
            validate_selection(graph.get_state(config).values, selection)
            owner["selection"] = selection
            self.cache.set_state(self._key(thread_id), owner)
            self._run(graph, Command(resume=selection), config)
            self.cache.set_state(self._key(thread_id), owner)
            return self._view(graph, config, session)

    def resume(self, thread_id, *, session):
        self._session(session)
        with self.cache.workflow_lock(str(thread_id)):
            owner = self._owned(thread_id, session)
            graph, config = self._graph(session), self._config(thread_id)
            view = self._view(graph, config, session)
            if not view.can_resume:
                return view
            if view.pending_input == "selection":
                raise CaseConflict("Submit an explicit supplier selection first")
            if view.pending_input == "manager_approval" and view.case.status == "AWAITING_MANAGER_APPROVAL":
                raise CaseConflict("Use the dedicated manager-decision endpoint before resuming")
            self._run(graph, Command(resume=True) if view.pending_input else None, config)
            self.cache.set_state(self._key(thread_id), owner)
            return self._view(graph, config, session)
