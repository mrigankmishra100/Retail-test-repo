"""Supplier-owned graph lifecycle; shared Oracle cases synchronize both roles."""
from uuid import uuid5
from langgraph.types import Command
from langsmith import tracing_context
from app.agents.checkpoints import CacheCheckpointer
from app.agents.supplier_graph import build_supplier_graph
from app.agents.tool_access import SupplierToolAccess, SUPPLIER_TOOL_NAMES
from app.agents.contracts import StartSupplierWorkflow, SupplierWorkflowResponse
from app.services.cases import CaseService, CaseConflict, CaseNotFound, digest
from app.oci.cache import CacheUnavailable


class SupplierWorkflowService:
    def __init__(self, container):
        self.container, self.cache = container, container.cache

    @staticmethod
    def _session(session):
        CaseService._session(session).require_role("supplier")

    @staticmethod
    def _key(thread_id):
        return "supplier_owner:" + str(thread_id)

    def _owned(self, thread_id, session):
        self._session(session)
        owner = self.cache.get_state(self._key(thread_id))
        if not owner or owner.get("session_id") != str(session.session_id) or owner.get("supplier_id") != session.supplier_id:
            raise CaseNotFound("Workflow not found or expired")
        self.container.case_service.get_case(owner["case_id"], session=session)
        return owner

    def _save_owner(self, thread_id, owner):
        if not self.cache.set_state(self._key(thread_id), owner):
            raise CacheUnavailable("Workflow ownership was not stored")

    def _graph(self, session):
        return build_supplier_graph(tools=SupplierToolAccess(self.container, session),
            cases=self.container.case_service, checkpointer=CacheCheckpointer(self.cache, namespace="supplier"))

    @staticmethod
    def _config(thread_id):
        return {"configurable": {"thread_id": str(thread_id)}, "recursion_limit": 35}

    @staticmethod
    def _run(graph, command, config):
        with tracing_context(enabled=False):
            graph.invoke(command, config, durability="sync")

    def _view(self, graph, config, session):
        snapshot = graph.get_state(config)
        if not snapshot.values:
            raise CaseNotFound("Workflow checkpoint not found or expired")
        state = snapshot.values
        pending = [i.value for task in snapshot.tasks for i in task.interrupts]
        case = self.container.case_service.get_case(state["case_id"], session=session)
        status = {"COMPLETED": "completed", "SUPPLIER_REJECTED": "supplier_rejected"}.get(case["status"], state["status"])
        message = state.get("message", "Workflow in progress; explicitly resume the pending step.")
        if status == "supplier_rejected":
            message = "Supplier rejected the case. No supplier response notification was queued. Retail can see the rejection."
        elif status == "completed":
            message = "The shared case is completed; both approved notifications were acknowledged."
        return SupplierWorkflowResponse(thread_id=config["configurable"]["thread_id"], status=status,
            pending_input=pending[0]["kind"] if pending else None, can_resume=bool(snapshot.next),
            message=message, error_code=state.get("error_code") or ("node_failed" if any(t.error for t in snapshot.tasks) else None),
            available_tools=sorted(SUPPLIER_TOOL_NAMES), case=case, inventory=state.get("inventory"),
            policy=state.get("policy"), quote=state.get("quote"),
            draft_response=(case.get("draft") or {}).get("supplier_response") or state.get("draft_response"),
            approved_memory=state.get("approved_memory"))

    def start(self, request, *, session, key):
        self._session(session)
        request = StartSupplierWorkflow.model_validate(request).model_dump(mode="json")
        CaseService._request(session, "supplier-workflow", request, key)
        # Validate visibility before creating any cache state.
        self.container.case_service.get_case(request["case_id"], session=session)
        thread_id = str(uuid5(session.session_id, "supplier-workflow:" + key))
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
                owner = {"session_id": str(session.session_id), "supplier_id": session.supplier_id,
                         "case_id": request["case_id"], "input_hash": digest(request)}
                self._save_owner(thread_id, owner)
            self._run(graph, request | {"thread_id": thread_id, "status": "starting"}, config)
            self._save_owner(thread_id, owner)
            return self._view(graph, config, session)

    def get(self, thread_id, *, session):
        with self.cache.workflow_lock(str(thread_id)):
            self._owned(thread_id, session)
            return self._view(self._graph(session), self._config(thread_id), session)

    def resume(self, thread_id, *, session):
        self._session(session)
        with self.cache.workflow_lock(str(thread_id)):
            owner = self._owned(thread_id, session)
            graph, config = self._graph(session), self._config(thread_id)
            view = self._view(graph, config, session)
            if not view.can_resume:
                return view
            if view.pending_input == "supplier_approval" and view.case.status == "AWAITING_SUPPLIER_APPROVAL":
                raise CaseConflict("Use the dedicated supplier-decision endpoint before resuming")
            self._run(graph, Command(resume=True) if view.pending_input else None, config)
            self._save_owner(thread_id, owner)
            return self._view(graph, config, session)
