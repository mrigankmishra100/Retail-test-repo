"""Policy adapter for Oracle Enterprise AI MCP Safety."""

from typing import Any

STANDARD_TOOLS = frozenset(
    {
        "get_inventory_risk",
        "get_item_sales_history",
        "query_retail_data",
        "search_policy_documents",
        "get_suppliers_for_item",
        "get_supplier_policy",
        "get_supplier_inventory",
        "calculate_supplier_quote",
        "create_replenishment_case",
        "draft_supplier_email",
        "get_approved_memory",
        "get_workflow_case",
        "prepare_workflow_case",
        "compare_workflow_suppliers",
        "get_case_approved_memory",
        "get_chat_history",
        "save_chat_turn",
    }
)
CONTROLLED_TOOLS = frozenset(
    {"send_supplier_request", "send_supplier_response", "close_replenishment_case"}
)

RETAIL_TOOLS = frozenset({"get_inventory_risk", "get_item_sales_history", "query_retail_data",
    "search_policy_documents",
    "get_suppliers_for_item", "create_replenishment_case", "send_supplier_request", "draft_supplier_email",
    "get_workflow_case", "prepare_workflow_case", "compare_workflow_suppliers", "get_case_approved_memory",
    "get_chat_history", "save_chat_turn"})
SUPPLIER_TOOLS = frozenset({"get_supplier_inventory", "calculate_supplier_quote", "send_supplier_response",
    "get_workflow_case", "get_case_approved_memory"})


class EnterpriseAIMCPSafetyClient:
    """Control the boundary between agent reasoning and enterprise actions.

    Controlled tools must be backed by workflow state proving the required
    human approval. This is a small adapter/policy seam, not a custom security
    framework.
    """

    def __init__(self, endpoint: str | None = None, enabled: bool = False) -> None:
        self.endpoint = endpoint
        self.enabled = enabled
        # TODO: Initialize the Oracle Enterprise AI MCP Safety client lazily.

    async def validate_tool_call(
        self,
        tool_name: str,
        workflow_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a fail-closed local rule before future hosted safety evaluation."""
        if tool_name not in STANDARD_TOOLS | CONTROLLED_TOOLS:
            return {"allowed": False, "reason": "unknown_tool"}
        context = workflow_context or {}
        # Context is built by the authenticated boundary or verified case service, never tool arguments.
        role = context.get("role")
        if role not in {"retail-manager", "supplier"} or not context.get("session_id"):
            return {"allowed": False, "reason": "trusted_session_required"}
        if ((tool_name in RETAIL_TOOLS - SUPPLIER_TOOLS and role != "retail-manager")
                or (tool_name in SUPPLIER_TOOLS - RETAIL_TOOLS and role != "supplier")):
            return {"allowed": False, "reason": "role_not_allowed"}
        if role == "supplier" and not context.get("supplier_id"):
            return {"allowed": False, "reason": "supplier_identity_required"}
        if tool_name in CONTROLLED_TOOLS and not (context.get("human_approved") is True and context.get("approval_event_ids")):
            return {"allowed": False, "reason": "human_approval_required"}
        if not self.enabled:
            return {"allowed": True, "reason": "local_policy_passed"}
        # TODO: Submit the invocation to Oracle Enterprise AI MCP Safety.
        raise NotImplementedError("Oracle Enterprise AI MCP Safety integration is pending.")
