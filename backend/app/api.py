"""Version 1 deterministic inventory, policy, session and chat APIs."""

from typing import Any

from fastapi import APIRouter, Depends, Query, HTTPException
from app.contracts import (ChatRequest, ChatResponse, InventoryRiskReportResponse,
                           SalesPage, ItemId, SupplierInventoryResponse, PolicyResponse)
from app.case_api import invoke, router as case_router
from pydantic import BaseModel

from app.dependencies import get_container
from app.services.inventory import InventoryService
from app.services.sessions import SessionContext
from app.session_api import current_session, retail_session, router as session_router
from app.retail_workflow_api import router as retail_workflow_router
from app.supplier_workflow_api import router as supplier_workflow_router
from app.registry_api import router as registry_router
from app.contracts import SupplierEmailPreview, ApprovedMemoryPage
from uuid import UUID

router = APIRouter()
router.include_router(session_router)
router.include_router(case_router)
router.include_router(retail_workflow_router)
router.include_router(supplier_workflow_router)
router.include_router(registry_router)


class ApprovalRequest(BaseModel):
    """A human decision for a pending commercial action."""

    case_id: str
    approved: bool
    comments: str | None = None




def get_inventory_service() -> InventoryService:
    """Resolve the configured inventory service for HTTP requests."""
    return get_container().inventory_service


@router.get("/inventory/risks", response_model=InventoryRiskReportResponse)
def get_inventory_risks(
    horizon_days: int | None = Query(default=None, ge=1, le=365),
    inventory_service: InventoryService = Depends(get_inventory_service),
    session: SessionContext = Depends(retail_session),
) -> dict[str, Any]:
    """Return deterministic risk assessments based on current Oracle data."""
    return invoke(inventory_service.get_inventory_risk_report, horizon_days)


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, session: SessionContext = Depends(current_session)) -> dict:
    """Read-only assistance; explicit conversation intent invokes the retail OCI graph."""
    if ((request.role is not None and request.role != session.role)
            or (request.session_id is not None and request.session_id != session.session_id)):
        raise HTTPException(403, "Chat identity must match the server session")
    if request.intent == "conversation":
        return invoke(get_container().retail_conversation_service.respond, request, session=session)
    return invoke(get_container().chat_service.respond, request, session=session)


@router.get("/cases/{case_id}/supplier-email", response_model=SupplierEmailPreview)
def supplier_email(case_id: UUID, session: SessionContext = Depends(retail_session)):
    return invoke(get_container().case_service.draft_supplier_email, str(case_id), session=session)


@router.get("/memory/approved", response_model=ApprovedMemoryPage)
def approved_memory(item_id: ItemId | None = None, limit: int = Query(default=10, ge=1, le=20),
                    offset: int = Query(default=0, ge=0, le=10000), session: SessionContext = Depends(current_session)):
    return invoke(get_container().memory_service.list_approved, session=session, item_id=item_id, limit=limit, offset=offset)


@router.post("/approve", deprecated=True)
def approve(request: ApprovalRequest, session: SessionContext = Depends(current_session)):
    """Legacy route intentionally disabled; only dedicated decisions can approve."""
    raise HTTPException(501, "Legacy approval route disabled; use /cases/{case_id}/manager-decision or supplier-decision")


@router.get("/supplier/items/{item_id}/inventory", response_model=SupplierInventoryResponse)
def get_own_supplier_inventory(item_id: ItemId, session: SessionContext = Depends(current_session)) -> dict:
    if session.role != "supplier":
        raise HTTPException(403, "Supplier session required")
    quantity = invoke(get_container().supplier_service.get_inventory, session.supplier_id, item_id, session=session)
    return {"supplier_id": session.supplier_id, "item_id": item_id, "available_quantity": quantity}


@router.get("/supplier/policy", response_model=PolicyResponse)
def get_own_supplier_policy(session: SessionContext = Depends(current_session)) -> dict:
    if session.role != "supplier":
        raise HTTPException(403, "Supplier session required")
    return invoke(get_container().policy_service.get_supplier_policy,
                  session.supplier_id, trusted_supplier_id=session.supplier_id)


@router.get("/items/{item_id}/sales-history", response_model=SalesPage)
def sales_history(item_id: ItemId, days: int = Query(default=30, ge=1, le=365),
                  limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000),
                  session: SessionContext = Depends(retail_session), service=Depends(get_inventory_service)):
    return invoke(service.get_sales_page, item_id, days, limit=limit, offset=offset)
