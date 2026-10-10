"""MCP-specific bounded inputs and safe supplier projections."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from pydantic import BaseModel, Field, field_validator
from app.contracts import ItemId, SupplierId, Money, CaseResponse, DispatchPending

Days = Annotated[int, Field(strict=True, ge=1, le=365)]
Quantity = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
Limit = Annotated[int, Field(strict=True, ge=1, le=100)]
Offset = Annotated[int, Field(strict=True, ge=0, le=10000)]
CaseId = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]
IdempotencyKey = Annotated[str, Field(pattern=r"^[!-~]{1,128}$")]
Question = Annotated[str, Field(min_length=1, max_length=4000)]
ChatContent = Annotated[str, Field(min_length=1, max_length=16000)]
ChatHistoryLimit = Annotated[int, Field(strict=True, ge=2, le=20)]
ChatTurnNumber = Annotated[int, Field(strict=True, ge=1)]
ChatRoute = Literal["LLM", "NL2SQL", "BLOCKED"]
ChatResponseStatus = Literal["COMPLETED", "FAILED", "BLOCKED"]
ChatGuardrailStatus = Literal["PASSED", "BLOCKED", "FAILED", "NOT_APPLIED"]
RetailDataset = Literal["inventory", "sales_summary", "supplier_options"]
RetailSort = Literal[
    "item_id", "stock_low_to_high", "stock_high_to_low", "sales_high_to_low",
    "sales_low_to_high", "price_low_to_high", "lead_time_low_to_high",
    "availability_high_to_low",
]


class RetailQueryResult(BaseModel):
    status: Literal["completed", "not_configured"]
    dataset: RetailDataset
    row_count: int = Field(ge=0, le=50)
    rows: list[dict[str, Any]]


class EligibleSupplier(BaseModel):
    supplier_id: SupplierId
    supplier_name: str
    item_id: ItemId
    base_price: Money
    lead_time_days: int
    minimum_order_quantity: int
    available_quantity: int | None
    inventory_last_updated: datetime | None = None

    @field_validator("base_price", mode="before")
    @classmethod
    def format_price(cls, value):
        return format(Decimal(str(value)), ".2f")


class EligibleSuppliers(BaseModel):
    item_id: ItemId
    suppliers: list[EligibleSupplier]


class DeliveryResult(BaseModel):
    """Explicit object envelope for the case-or-pending union required by MCP."""
    result: CaseResponse | DispatchPending
