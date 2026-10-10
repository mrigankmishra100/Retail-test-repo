"""Version 1 public HTTP contracts. Persistence and SDK objects stay private."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator, computed_field, field_validator
from app.services.case_state import CaseStatus

ItemId = Annotated[str, Field(pattern=r"^ITEM[0-9]{3}$")]
SupplierId = Annotated[str, Field(pattern=r"^SUP[0-9]{3}$")]
Money = Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]{2}$")]

class InventoryRiskRulesResponse(BaseModel):
    demand_window_days: int
    risk_horizon_days: int
    target_coverage_days: int


class InventoryRiskSummaryResponse(BaseModel):
    inventory_item_count: int
    at_risk_count: int
    not_at_risk_count: int
    missing_history_count: int


class InventoryHistoryQualityResponse(BaseModel):
    as_of_date: date
    last_sale_date: date
    missing_days: int
    is_stale: bool
    recommendation_is_provisional: bool
    warnings: list[str]


class InventoryRiskItemResponse(BaseModel):
    item_id: str
    item_name: str
    category: str
    current_stock: int
    reorder_point: int
    safety_stock: int
    demand_window_days: int
    observed_days: int
    history_quality: InventoryHistoryQualityResponse
    total_demand: float
    average_daily_demand: float
    risk_horizon_days: int
    horizon_demand: float
    projected_stock: float
    days_of_supply: float | None
    target_coverage_days: int
    target_stock: float
    recommended_quantity: int
    status: Literal["at_risk", "not_at_risk"]
    explanation: str


class UnassessedInventoryItemResponse(BaseModel):
    item_id: str
    item_name: str
    current_stock: int
    status: Literal["missing_history"]
    reason: str


class InventoryRiskReportResponse(BaseModel):
    rules: InventoryRiskRulesResponse
    summary: InventoryRiskSummaryResponse
    at_risk_items: list[InventoryRiskItemResponse]
    not_at_risk_items: list[InventoryRiskItemResponse]
    unassessed_items: list[UnassessedInventoryItemResponse]

class QuoteResponse(BaseModel):
    supplier_id: SupplierId
    supplier_name: str
    item_id: ItemId
    quantity: int
    minimum_order_quantity: int
    available_quantity: int | None
    currency: Literal["INR"]
    unit: str
    base_price: Money
    unit_price: Money
    discount_amount: Money
    subtotal: Money
    expedited: bool
    surcharge: Money | None
    surcharge_rate_percent: str | None
    priced_total: Money | None
    lead_time_days: int | None
    feasible: bool
    infeasibility_reasons: list[str]
    payment_terms: str
    delivery_terms: str
    special_conditions: str
    policy_sha256: str
    policy_source: Literal["object_storage", "local"]
    valid_until: date
    history_quality: InventoryHistoryQualityResponse | None
    exclusions: list[str]
    confirmation_required: bool
    requested_quantity: int | None = None
    quantity_adjusted: bool = False


class UnavailableQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplier_id: SupplierId
    feasible: Literal[False]
    infeasibility_reasons: list[str]
    priced_total: None
    lead_time_days: None


class ComparisonResponse(BaseModel):
    item_id: ItemId
    quantity: int
    suppliers: list[QuoteResponse | UnavailableQuote]
    recommended_supplier_id: SupplierId | None
    rationale: str


class SupplierEmailDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_version: Literal["supplier-request-v1", "supplier-request-v2", "supplier-request-v3"] = "supplier-request-v1"
    delivery_mode: Literal["supplier_topic", "shared_poc_topic"] = "supplier_topic"
    recipient: str = Field(min_length=3, max_length=254, pattern=r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
    subject: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")
    body: str = Field(min_length=1, max_length=16000)


class SupplierResponseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_version: Literal["supplier-response-v1", "supplier-response-v2", "supplier-response-v3", "supplier-response-v4"] = "supplier-response-v1"
    delivery_mode: Literal["case_update_topic", "shared_poc_topic"] = "case_update_topic"
    subject: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")
    body: str = Field(min_length=1, max_length=16000)


class CaseDraft(BaseModel):
    item_id: ItemId
    quantity: int
    supplier_id: SupplierId
    quote: QuoteResponse
    scope: str
    supplier_email: SupplierEmailDraft | None = None
    supplier_response: SupplierResponseDraft | None = None


class SupplierEmailPreview(BaseModel):
    case_id: UUID
    draft_hash: str
    version: int
    email: SupplierEmailDraft
    approval_recorded: Literal[False] = False


class ApprovedMemoryRecord(BaseModel):
    case_id: UUID
    status: Literal["COMPLETED"]
    approved_terms: CaseDraft


class ApprovedMemoryPage(BaseModel):
    items: list[ApprovedMemoryRecord]
    limit: int
    offset: int
    has_more: bool


class DeliveryState(BaseModel):
    type: Literal["SUPPLIER_REQUEST", "SUPPLIER_RESPONSE"]
    status: Literal["PENDING", "SENDING", "SENT", "FAILED", "UNKNOWN"]


class CaseDecisionView(BaseModel):
    role: Literal["retail-manager", "supplier"]
    decision: Literal["APPROVE", "REJECT"]
    comment: str | None = None


class CaseResponse(BaseModel):
    case_id: UUID
    item_id: ItemId
    current_stock: int
    recommended_quantity: int
    selected_supplier_id: SupplierId | None
    status: CaseStatus
    version: int
    draft_hash: str | None
    draft: CaseDraft | None
    summary: str | None = None
    notifications: list[DeliveryState] = Field(default_factory=list)
    decisions: list[CaseDecisionView] = Field(default_factory=list)

    @computed_field
    @property
    def pending_approval(self) -> Literal["manager", "supplier"] | None:
        return {CaseStatus.AWAITING_MANAGER_APPROVAL: "manager",
                CaseStatus.AWAITING_SUPPLIER_APPROVAL: "supplier"}.get(self.status)


class WorkflowCaseResponse(CaseResponse):
    """Internal workflow snapshot that validates, but does not rewrite, stored draft JSON."""
    draft: dict | None

    @field_validator("draft")
    @classmethod
    def validate_raw_draft(cls, value):
        if value is not None:
            CaseDraft.model_validate(value)
        return value


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["retail-inventory-agent"]


class DispatchPending(BaseModel):
    case_id: UUID
    delivery_status: Literal["PENDING", "SENDING", "FAILED", "UNKNOWN"]


class CaseListItem(BaseModel):
    case_id: UUID
    item_id: ItemId
    recommended_quantity: int
    selected_supplier_id: SupplierId | None
    status: CaseStatus
    version: int
    request_notification_status: Literal["PENDING", "SENDING", "SENT", "FAILED", "UNKNOWN"] | None = None
    response_notification_status: Literal["PENDING", "SENDING", "SENT", "FAILED", "UNKNOWN"] | None = None


class CasePage(BaseModel):
    items: list[CaseListItem]
    limit: int
    offset: int
    has_more: bool


class SaleResponse(BaseModel):
    sale_id: str
    item_id: ItemId
    sale_date: datetime
    quantity_sold: int


class SalesPage(BaseModel):
    item_id: ItemId
    days: int
    items: list[SaleResponse]
    limit: int
    offset: int
    has_more: bool


class SupplierInventoryResponse(BaseModel):
    supplier_id: SupplierId
    item_id: ItemId
    available_quantity: int | None


class PolicyResponse(BaseModel):
    supplier_id: SupplierId
    object_name: str
    content: str
    sha256: str
    page_count: Literal[1]
    document_loaded: Literal[True]
    source: Literal["object_storage", "local"]
    fallback_reason: str | None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    intent: Literal["help", "inventory_risks", "supplier_quote", "case_status", "conversation"] = "help"
    conversation_id: UUID | None = None
    workflow_thread_id: UUID | None = None
    item_id: ItemId | None = None
    case_id: UUID | None = None
    quantity: int | None = Field(default=None, strict=True, ge=1, le=1_000_000)
    # Optional legacy claims are checked, never trusted as identity.
    session_id: UUID | None = None
    role: Literal["retail-manager", "supplier"] | None = None

    @model_validator(mode="after")
    def intent_fields(self):
        if self.intent != "conversation" and (self.conversation_id or self.workflow_thread_id):
            raise ValueError("Conversation identifiers require conversation intent")
        if self.intent == "conversation" and (self.item_id is not None or self.case_id is not None or self.quantity is not None):
            raise ValueError("Use message and optional workflow_thread_id for conversation context")
        if self.intent == "supplier_quote" and (self.item_id is None or self.quantity is None):
            raise ValueError("supplier_quote requires item_id and quantity")
        if self.intent == "case_status" and self.case_id is None:
            raise ValueError("case_status requires case_id")
        return self


class ChatResponse(BaseModel):
    mode: Literal["deterministic_read_only", "llm_read_only"] = "deterministic_read_only"
    message: str
    approval_recorded: Literal[False] = False
    pending_approval: Literal["manager", "supplier"] | None = None
    allowed_actions: list[str]
    case: CaseResponse | None = None
    quote: QuoteResponse | None = None
    inventory: InventoryRiskReportResponse | None = None
    conversation_id: UUID | None = None
    workflow_thread_id: UUID | None = None
    llm_status: Literal["disabled", "completed", "limit_reached", "blocked"] = "disabled"
    used_tools: list[str] = Field(default_factory=list)
    response_route: Literal["LLM", "NL2SQL", "BLOCKED"] | None = None
    guardrail_status: Literal["PASSED", "BLOCKED", "FAILED", "NOT_APPLIED"] | None = None


class ChatHistoryMessage(BaseModel):
    turn_number: int = Field(strict=True, ge=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)
    response_route: Literal["LLM", "NL2SQL", "BLOCKED"] | None = None
    response_status: Literal["PENDING", "COMPLETED", "FAILED", "BLOCKED"] | None = None
    guardrail_status: Literal["PASSED", "BLOCKED", "FAILED", "NOT_APPLIED"] | None = None
    evidence_metadata: dict | None = None
    error_code: str | None = None
    created_at: datetime | None = None


class ChatHistoryResponse(BaseModel):
    conversation_id: UUID
    workflow_thread_id: UUID | None = None
    exists: bool
    next_turn: int = Field(strict=True, ge=1)
    messages: list[ChatHistoryMessage]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, str]


class ErrorField(BaseModel):
    location: list[str | int]
    code: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    trace_id: UUID
    fields: list[ErrorField] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorDetail
