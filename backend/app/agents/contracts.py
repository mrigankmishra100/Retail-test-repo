"""Public retail workflow controls; never accept graph state or approval context."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from app.contracts import (ItemId, SupplierId, CaseResponse, ComparisonResponse, InventoryRiskReportResponse,
    SupplierInventoryResponse, PolicyResponse, QuoteResponse, ApprovedMemoryRecord, SupplierResponseDraft)


class StartRetailWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: ItemId | None = None
    quantity: int | None = Field(default=None, strict=True, ge=1, le=1_000_000)


class RetailSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: SupplierId
    quantity: int = Field(strict=True, ge=1, le=1_000_000)


class ResumeRetailWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetailWorkflowResponse(BaseModel):
    thread_id: UUID
    mode: Literal["deterministic_retail_graph"] = "deterministic_retail_graph"
    status: Literal["starting", "assessed", "awaiting_selection", "selected", "draft_created",
        "awaiting_approval", "approved", "manager_rejected", "request_sent", "no_risk",
        "insufficient_history", "no_feasible_supplier", "delivery_blocked", "delivery_pending",
        "delivery_reconciliation_required", "supplier_rejected", "response_queued", "supplier_responded", "completed"]
    pending_input: Literal["selection", "manager_approval", "delivery"] | None
    can_resume: bool
    message: str
    error_code: str | None
    available_tools: list[str]
    inventory: InventoryRiskReportResponse | None = None
    comparison: ComparisonResponse | None = None
    case: CaseResponse | None = None
    advisory: str | None = None
    llm_status: Literal["disabled", "completed", "unavailable", "limit_reached"] = "disabled"


class StartSupplierWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: UUID


class ResumeSupplierWorkflow(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupplierWorkflowResponse(BaseModel):
    thread_id: UUID
    mode: Literal["deterministic_supplier_graph"] = "deterministic_supplier_graph"
    status: Literal["starting", "received", "inventory_checked", "policy_checked", "quoted",
        "awaiting_approval", "approved", "supplier_rejected", "review_blocked", "delivery_blocked",
        "delivery_pending", "delivery_reconciliation_required", "response_sent", "completed"]
    pending_input: Literal["supplier_approval", "review", "delivery"] | None
    can_resume: bool
    message: str
    error_code: str | None = None
    available_tools: list[str]
    case: CaseResponse
    inventory: SupplierInventoryResponse | None = None
    policy: PolicyResponse | None = None
    quote: QuoteResponse | None = None
    draft_response: SupplierResponseDraft | None = None
    approved_memory: ApprovedMemoryRecord | None = None
