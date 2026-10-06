"""Internal trusted tool binding; no session/approval argument enters a public tool schema."""
from contextvars import ContextVar
from dataclasses import dataclass
from app.services.cases import CaseService

binding = ContextVar("internal_retail_tool_binding", default=None)
SUPPLIER_TOOL_NAMES = frozenset({"get_supplier_inventory", "get_supplier_policy", "calculate_supplier_quote",
    "send_supplier_response", "close_replenishment_case", "get_approved_memory",
    "get_workflow_case", "get_case_approved_memory"})
RETAIL_TOOL_NAMES = frozenset({"get_inventory_risk", "get_item_sales_history", "query_retail_data", "get_suppliers_for_item",
    "search_policy_documents",
    "get_supplier_policy", "create_replenishment_case", "send_supplier_request", "close_replenishment_case",
    "draft_supplier_email", "get_approved_memory", "get_workflow_case", "prepare_workflow_case",
    "compare_workflow_suppliers", "get_case_approved_memory", "get_chat_history", "save_chat_turn"})


@dataclass(frozen=True)
class RetailToolAccess:
    container: object
    session: object

    def call(self, name, **arguments):
        CaseService._session(self.session).require_role("retail-manager")
        if name not in RETAIL_TOOL_NAMES:
            raise PermissionError("Tool not available to the retail workflow")
        if getattr(getattr(self.container, 'settings', None), 'remote_mcp_enabled', False):
            from app.mcp.remote import call_remote
            return call_remote(self.container, self.session, name, arguments)
        from app.mcp.tools import TOOL_FUNCTIONS
        token = binding.set(self)
        try:
            return TOOL_FUNCTIONS[name](**arguments).model_dump(mode="json")
        finally:
            binding.reset(token)


@dataclass(frozen=True)
class SupplierToolAccess:
    container: object
    session: object

    def call(self, name, **arguments):
        CaseService._session(self.session).require_role("supplier")
        if name not in SUPPLIER_TOOL_NAMES:
            raise PermissionError("Tool not available to the supplier workflow")
        if getattr(getattr(self.container, 'settings', None), 'remote_mcp_enabled', False):
            from app.mcp.remote import call_remote
            return call_remote(self.container, self.session, name, arguments)
        from app.mcp.tools import TOOL_FUNCTIONS
        token = binding.set(self)
        try:
            return TOOL_FUNCTIONS[name](**arguments).model_dump(mode="json")
        finally:
            binding.reset(token)
