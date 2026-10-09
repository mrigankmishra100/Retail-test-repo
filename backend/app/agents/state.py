"""Checkpoint-safe state definitions; identity lives outside graph state."""

from typing import Any, TypedDict


class RetailAgentState(TypedDict, total=False):
    """State carried by the Retail Manager Agent."""

    case_id: str | None
    thread_id: str
    item_id: str | None
    quantity: int | None
    inventory: dict[str, Any]
    comparison: dict[str, Any] | None
    selection: dict[str, Any] | None
    case: dict[str, Any] | None
    status: str
    message: str
    error_code: str | None
    advisory: str | None
    llm_status: str


class SupplierAgentState(TypedDict, total=False):
    """Supplier facts only; identity authority never comes from a checkpoint."""
    thread_id: str
    case_id: str
    case: dict[str, Any]
    inventory: dict[str, Any] | None
    policy: dict[str, Any] | None
    quote: dict[str, Any] | None
    draft_response: dict[str, Any] | None
    approved_memory: dict[str, Any] | None
    status: str
    message: str
    error_code: str | None
