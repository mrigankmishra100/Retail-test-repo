"""Assigned supplier workflow; Oracle decisions, not resume text, authorize actions."""
from functools import wraps
import logging
from fastmcp.exceptions import ToolError
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from app.agents.state import SupplierAgentState
from app.services.cases import CaseConflict, digest
from app.services.supplier_response import draft_supplier_response
from app.contracts import QuoteResponse

logger = logging.getLogger(__name__)


def build_supplier_graph(*, tools, cases, checkpointer):
    def node(fn):
        @wraps(fn)
        def run(state):
            cases._session(tools.session).require_role("supplier")
            cases.get_case(state["case_id"], session=tools.session)
            logger.info("Supplier graph node=%s", fn.__name__, extra={"graph_node": fn.__name__,
                "role": tools.session.role, "supplier_id": tools.session.supplier_id,
                "session_id": str(tools.session.session_id), "case_id": state["case_id"]})
            return fn(state)
        return run

    def blocked(code):
        return {"status": "review_blocked", "error_code": code,
            "message": "Review is blocked. Reject through the supplier-decision control, or fix the facts and explicitly resume."}

    @node
    def receive(state):
        case = cases.get_case(state["case_id"], session=tools.session)
        status = {"SUPPLIER_REJECTED": "supplier_rejected", "RESPONSE_QUEUED": "approved",
            "SUPPLIER_RESPONDED": "response_sent", "COMPLETED": "completed"}.get(case["status"], "received")
        if status == "received" and case["status"] != "AWAITING_SUPPLIER_APPROVAL":
            raise CaseConflict("Request is not ready for supplier review")
        if not case.get("draft") or digest(case["draft"]) != case["draft_hash"]:
            raise CaseConflict("Case draft integrity check failed")
        return {"case": case, "status": status, "error_code": None, "inventory": None,
            "policy": None, "quote": None, "draft_response": None,
            "message": "Loaded the assigned request and current persisted decision."}

    @node
    def inventory(state):
        try:
            result = tools.call("get_supplier_inventory", supplier_id=tools.session.supplier_id,
                                item_id=state["case"]["item_id"])
        except ToolError as exc:
            return blocked(str(exc).split(";", 1)[0])
        quantity = result["available_quantity"]
        if quantity is None or quantity < state["case"]["recommended_quantity"]:
            return {"inventory": result} | blocked("supplier_stock_unknown" if quantity is None else "insufficient_supplier_stock")
        return {"inventory": result, "status": "inventory_checked"}

    @node
    def policy(state):
        try:
            result = tools.call("get_supplier_policy", supplier_id=tools.session.supplier_id)
        except ToolError as exc:
            return blocked(str(exc).split(";", 1)[0])
        return {"policy": result, "status": "policy_checked"}

    @node
    def quote(state):
        try:
            result = tools.call("calculate_supplier_quote", supplier_id=tools.session.supplier_id,
                item_id=state["case"]["item_id"], quantity=state["case"]["recommended_quantity"])
        except ToolError as exc:
            return blocked(str(exc).split(";", 1)[0])
        ignored = {"available_quantity", "history_quality", "policy_source"}
        original = QuoteResponse.model_validate(state["case"]["draft"]["quote"]).model_dump(mode="json")
        if not result["feasible"]:
            return {"quote": result} | blocked("quote_infeasible")
        if {k: v for k, v in result.items() if k not in ignored} != {k: v for k, v in original.items() if k not in ignored}:
            return {"quote": result} | blocked("quote_changed")
        return {"quote": result, "status": "quoted"}

    @node
    def draft(state):
        payload = state["case"]["draft"]
        response = payload.get("supplier_response") or draft_supplier_response(state["case_id"], payload)
        return {"draft_response": response, "status": "awaiting_approval",
            "message": "Review the stored request and response terms. Use the dedicated supplier-decision endpoint."}

    @node
    def approval(state):
        case = cases.get_case(state["case_id"], session=tools.session)
        if case["status"] == "AWAITING_SUPPLIER_APPROVAL":
            interrupt({"kind": "supplier_approval", "case_id": state["case_id"]})
            case = cases.get_case(state["case_id"], session=tools.session)
        if case["status"] == "AWAITING_SUPPLIER_APPROVAL":
            raise CaseConflict("A stored supplier decision is required before resuming")
        status = {"SUPPLIER_REJECTED": "supplier_rejected", "RESPONSE_QUEUED": "approved",
            "SUPPLIER_RESPONDED": "response_sent", "COMPLETED": "completed"}.get(case["status"])
        if status is None:
            raise CaseConflict("Unexpected supplier decision state")
        return {"case": case, "status": status, "message": "Supplier decision loaded from Oracle."}

    @node
    def dispatch(state):
        try:
            result = tools.call("send_supplier_response", case_id=state["case_id"],
                                supplier_id=tools.session.supplier_id)["result"]
        except ToolError as exc:
            case = cases.get_case(state["case_id"], session=tools.session)
            uncertain = any(n["type"] == "SUPPLIER_RESPONSE" and n["status"] in {"FAILED", "UNKNOWN"}
                            for n in case["notifications"])
            return {"case": case, "status": "delivery_reconciliation_required" if uncertain else "delivery_blocked",
                "error_code": str(exc).split(";", 1)[0], "message": "Delivery did not complete. Check persisted delivery state before retrying."}
        if "delivery_status" in result:
            return {"status": "delivery_reconciliation_required" if result["delivery_status"] in {"FAILED", "UNKNOWN"} else "delivery_pending",
                "message": "Delivery has not been acknowledged; uncertain results require operator reconciliation."}
        return {"case": result, "status": "response_sent", "error_code": None,
            "message": "Supplier response notification acknowledged; finalizing approved case."}

    @node
    def complete(state):
        case = tools.call("close_replenishment_case", case_id=state["case_id"],
                          idempotency_key="supplier-graph:" + state["thread_id"] + ":complete")
        return {"case": case, "status": "completed"}

    @node
    def memory(state):
        record = cases.approved_memory(state["case_id"], session=tools.session)
        return {"approved_memory": record, "status": "completed", "error_code": None,
            "message": "Case completed and approved summary verified. Retail and supplier share this persisted case."}

    @node
    def wait_review(state):
        interrupt({"kind": "review", "case_id": state["case_id"]})
        return {"error_code": None}

    @node
    def wait_delivery(state):
        interrupt({"kind": "delivery", "case_id": state["case_id"]})
        return {"error_code": None}

    def decision_route(state):
        return {"supplier_rejected": END, "approved": "dispatch", "response_sent": "complete",
                "completed": "memory"}.get(state["status"], "inventory")

    graph = StateGraph(SupplierAgentState)
    for fn in (receive, inventory, policy, quote, draft, approval, dispatch, complete, memory, wait_review, wait_delivery):
        graph.add_node(fn.__name__, fn)
    graph.add_edge(START, "receive")
    routes = {END: END, "dispatch": "dispatch", "complete": "complete", "memory": "memory", "inventory": "inventory"}
    graph.add_conditional_edges("receive", decision_route, routes)
    for name, following in (("inventory", "policy"), ("policy", "quote"), ("quote", "draft")):
        graph.add_conditional_edges(name, lambda s, following=following: "wait_review" if s["status"] == "review_blocked" else following,
                                    {"wait_review": "wait_review", following: following})
    graph.add_edge("wait_review", "receive")
    graph.add_edge("draft", "approval")
    graph.add_conditional_edges("approval", decision_route, {k: v for k, v in routes.items() if k != "inventory"})
    graph.add_conditional_edges("dispatch", lambda s: "complete" if s["status"] == "response_sent" else (
        "wait_delivery" if s["status"] in {"delivery_blocked", "delivery_pending"} else END),
        {"complete": "complete", "wait_delivery": "wait_delivery", END: END})
    graph.add_edge("wait_delivery", "dispatch")
    graph.add_edge("complete", "memory")
    graph.add_edge("memory", END)
    return graph.compile(checkpointer=checkpointer)
