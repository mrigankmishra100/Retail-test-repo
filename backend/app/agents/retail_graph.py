"""Deterministic retail LangGraph; only stored human decisions authorize dispatch."""
from functools import wraps
import logging
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from fastmcp.exceptions import ToolError
from app.agents.state import RetailAgentState
from app.contracts import ComparisonResponse
from app.services.cases import CaseConflict
from app.services.inventory import ItemNotFound

logger = logging.getLogger(__name__)


def validate_selection(state, selection):
    for quote in state["comparison"]["suppliers"]:
        if quote["supplier_id"] == selection["supplier_id"] and quote["feasible"]:
            if quote["quantity"] == selection["quantity"]:
                return
    raise CaseConflict("Select a feasible supplier and its displayed MOQ-adjusted quantity")


def build_retail_graph(*, tools, cases, suppliers, checkpointer, conversation=None):
    """Build with trusted per-invocation dependencies, never persisted bearer credentials."""
    def node(fn):
        @wraps(fn)
        def run(state):
            cases._session(tools.session).require_role("retail-manager")
            logger.info("Retail graph node=%s", fn.__name__, extra={"graph_node": fn.__name__,
                "role": tools.session.role, "session_id": str(tools.session.session_id),
                "case_id": state.get("case_id")})
            return fn(state)
        return run

    @node
    def analyze(state):
        report = tools.call("get_inventory_risk")
        chosen = state.get("item_id")
        if not chosen:
            chosen = next((r["item_id"] for r in report["at_risk_items"]), None)
        base = {"inventory": report, "item_id": chosen, "error_code": None}
        if chosen is None:
            missing = bool(report["unassessed_items"])
            return base | {"status": "insufficient_history" if missing else "no_risk",
                "message": "No assessed risk found; some demand is unknown." if missing else "No assessed replenishment risk found."}
        risk = next((r for r in report["at_risk_items"] if r["item_id"] == chosen), None)
        if risk is None:
            missing = any(r["item_id"] == chosen for r in report["unassessed_items"])
            if not missing and not any(r["item_id"] == chosen for r in report["not_at_risk_items"]):
                raise ItemNotFound("Item not found")
            return base | {"status": "insufficient_history" if missing else "no_risk",
                "message": "Demand history is unavailable." if missing else "The selected item has no assessed replenishment risk."}
        return base | {"quantity": state.get("quantity") or risk["recommended_quantity"], "status": "assessed"}

    @node
    def compare(state):
        # The public tools supply factual lookups; the shared deterministic comparator owns pricing/ranking.
        tools.call("get_suppliers_for_item", item_id=state["item_id"])
        result = ComparisonResponse.model_validate(suppliers.compare(state["item_id"], state["quantity"])).model_dump(mode="json")
        feasible = any(q["feasible"] for q in result["suppliers"])
        return {"comparison": result, "status": "awaiting_selection" if feasible else "no_feasible_supplier",
            "message": "Review supplier terms and choose a displayed quantity." if feasible else "No actionable supplier offer; review feasibility and policy warnings."}

    @node
    def explain(state):
        if conversation is None:
            return {"advisory": None, "llm_status": "disabled"}
        return conversation.explain(session=tools.session,
            facts={"inventory": state["inventory"], "comparison": state.get("comparison"),
                   "item_id": state.get("item_id"), "quantity": state.get("quantity"), "status": state["status"]})

    @node
    def select(state):
        selection = interrupt({"kind": "selection"})
        validate_selection(state, selection)
        return {"selection": selection, "status": "selected"}

    @node
    def create(state):
        case = tools.call("create_replenishment_case", item_id=state["item_id"],
            quantity=state["selection"]["quantity"], idempotency_key="graph:" + state["thread_id"] + ":create")
        return {"case_id": case["case_id"], "case": case, "status": "draft_created"}

    @node
    def prepare(state):
        case = cases.prepare(state["case_id"], state["selection"]["supplier_id"], session=tools.session,
            version=0, key="graph:" + state["thread_id"] + ":prepare")
        return {"case": case, "status": "awaiting_approval",
            "message": "Review this exact draft; approve or reject through the dedicated manager-decision endpoint."}

    @node
    def approval(state):
        case = cases.get_case(state["case_id"], session=tools.session)
        if case["status"] == "AWAITING_MANAGER_APPROVAL":
            interrupt({"kind": "manager_approval", "case_id": state["case_id"]})
            # Resume values are deliberately ignored. Oracle is the only approval authority.
            case = cases.get_case(state["case_id"], session=tools.session)
        if case["status"] == "MANAGER_REJECTED":
            return {"case": case, "status": "manager_rejected", "message": "The manager rejected the request. Nothing was sent."}
        if case["status"] in {"DRAFT", "AWAITING_MANAGER_APPROVAL"}:
            raise CaseConflict("A stored manager decision is required before resuming")
        return {"case": case, "status": "approved"}

    @node
    def dispatch(state):
        try:
            result = tools.call("send_supplier_request", case_id=state["case_id"],
                supplier_id=state["selection"]["supplier_id"])["result"]
        except ToolError as exc:
            code = str(exc).split(";", 1)[0]
            return {"status": "delivery_blocked", "error_code": code,
                "message": "Dispatch did not complete. Check the case and dependencies before an explicit retry."}
        if "delivery_status" in result:
            uncertain = result["delivery_status"] in {"UNKNOWN", "FAILED"}
            return {"status": "delivery_reconciliation_required" if uncertain else "delivery_pending",
                "message": "Delivery requires operator review; do not resend." if uncertain else "Delivery is pending; check before retrying.",
                "error_code": None}
        return {"case": result, "status": "request_sent", "error_code": None,
            "message": "The approved request was acknowledged. Supplier review proceeds separately."}

    @node
    def wait_delivery(state):
        interrupt({"kind": "delivery", "case_id": state["case_id"]})
        return {"error_code": None}

    graph = StateGraph(RetailAgentState)
    for fn in (analyze, compare, explain, select, create, prepare, approval, dispatch, wait_delivery):
        graph.add_node(fn.__name__, fn)
    graph.add_edge(START, "analyze")
    graph.add_conditional_edges("analyze", lambda s: "compare" if s["status"] == "assessed" else END,
                                {"compare": "compare", END: END})
    graph.add_edge("compare", "explain")
    graph.add_conditional_edges("explain", lambda s: "select" if s["status"] == "awaiting_selection" else END,
                                {"select": "select", END: END})
    graph.add_edge("select", "create")
    graph.add_edge("create", "prepare")
    graph.add_edge("prepare", "approval")
    graph.add_conditional_edges("approval", lambda s: END if s["status"] == "manager_rejected" else "dispatch",
                                {END: END, "dispatch": "dispatch"})
    graph.add_conditional_edges("dispatch", lambda s: "wait_delivery" if s["status"] in {"delivery_blocked", "delivery_pending"} else END,
                                {"wait_delivery": "wait_delivery", END: END})
    graph.add_edge("wait_delivery", "dispatch")
    return graph.compile(checkpointer=checkpointer)
