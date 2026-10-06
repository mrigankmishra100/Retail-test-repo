"""Role-aware read-only conversational entry point for the Phase 6 deterministic API."""
import re


class ChatService:
    def __init__(self, inventory, suppliers, cases):
        self.inventory, self.suppliers, self.cases = inventory, suppliers, cases

    def respond(self, request, *, session):
        self.cases._session(session)
        actions = ["view_cases", "view_inventory_risks"] if session.role == "retail-manager" else [
            "view_assigned_cases", "view_own_inventory", "view_own_quote"]
        base = {"allowed_actions": actions, "approval_recorded": False, "pending_approval": None}
        if re.search(r"\b(approve|approved|reject|rejected|send|complete|confirm|purchase|order)\b", request.message, re.I):
            return base | {"message": "Chat does not record decisions or send messages. Review the current case and use its dedicated approval controls."}
        if request.intent == "inventory_risks":
            session.require_role("retail-manager")
            report = self.inventory.get_inventory_risk_report()
            risk_count = len(report["at_risk_items"])
            missing_count = len(report["unassessed_items"])
            if risk_count == 1:
                message = "1 item currently requires replenishment review."
            elif risk_count:
                message = f"{risk_count} items currently require replenishment review."
            else:
                message = "No items currently require replenishment review."
            if missing_count:
                message += (f" {missing_count} item{'s' if missing_count != 1 else ''} could not be assessed "
                            "because sales history is unavailable.")
            return base | {"message": message, "inventory": report}
        if request.intent == "supplier_quote":
            session.require_role("supplier")
            return base | {"message": "This is a policy-scoped quote, not a commitment. Review its exclusions and feasibility.",
                           "quote": self.suppliers.calculate_quote(session.supplier_id, request.item_id,
                                                                 request.quantity, session=session)}
        if request.intent == "case_status":
            case = self.cases.get_case(str(request.case_id), session=session)
            pending = {"AWAITING_MANAGER_APPROVAL": "manager", "AWAITING_SUPPLIER_APPROVAL": "supplier"}.get(case["status"])
            can_decide = (pending == "manager" and session.role == "retail-manager") or (pending == "supplier" and session.role == "supplier")
            return base | {"case": case, "pending_approval": pending,
                           "allowed_actions": actions + (["review_" + pending + "_decision"] if can_decide else []),
                           "message": "The case status below is authoritative. No decision was recorded by this chat request."}
        return base | {"message": "I can show inventory risks (retail), your own quote (supplier), or an accessible case's status. Choose the matching intent and identifiers. This read-only assistant does not run an agent workflow."}
