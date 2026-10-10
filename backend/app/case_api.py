"""Dedicated deterministic case/quote endpoints; never accept decisions through chat/MCP."""

from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_container
from app.session_api import current_session, retail_session
from app.services.sessions import SessionContext
from app.services.cases import CaseConflict, CaseNotFound
from app.services.policy import PolicyDocumentError
from app.oci.notifications import NotificationDeliveryError
from app.services.inventory import ItemNotFound
from app.oci.cache import WorkflowBusy
from app.contracts import CaseResponse, DispatchPending, ComparisonResponse, QuoteResponse, CasePage, ItemId

router = APIRouter()


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCase(StrictBody):
    item_id: str = Field(pattern=r"^ITEM[0-9]{3}$")
    quantity: int = Field(strict=True, ge=1, le=1_000_000)


class PrepareCase(StrictBody):
    supplier_id: str = Field(pattern=r"^SUP[0-9]{3}$")
    version: int = Field(strict=True, ge=0)


class Decision(StrictBody):
    approved: bool = Field(strict=True)
    version: int = Field(strict=True, ge=0)
    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    comment: str | None = Field(default=None, max_length=2000, description="At most 2000 UTF-8 bytes")


def case_service():
    return get_container().case_service


def invoke(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except (CaseNotFound, ItemNotFound) as exc:
        raise HTTPException(404, "Resource not found") from exc
    except (CaseConflict, WorkflowBusy) as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except PolicyDocumentError as exc:
        raise HTTPException(503, {"code": "policy_unavailable"}) from exc
    except NotificationDeliveryError as exc:
        code = "publishing_disabled" if exc.code == "notification_publishing_disabled" else "dependency_unavailable"
        raise HTTPException(503, {"code": code}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Operation unavailable; retry with the same idempotency key") from exc


@router.post("/cases", response_model=CaseResponse, status_code=201)
def create_case(body: CreateCase, session: SessionContext = Depends(retail_session),
                key: str = Header(alias="Idempotency-Key"), service=Depends(case_service)):
    return invoke(service.create_case, body.item_id, body.quantity, session=session, key=key)


@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: UUID, session: SessionContext = Depends(current_session), service=Depends(case_service)):
    return invoke(service.get_case, str(case_id), session=session)


@router.post("/cases/{case_id}/prepare", response_model=CaseResponse)
def prepare_case(case_id: UUID, body: PrepareCase, session: SessionContext = Depends(retail_session),
                 key: str = Header(alias="Idempotency-Key"), service=Depends(case_service)):
    return invoke(service.prepare, str(case_id), body.supplier_id, session=session, version=body.version, key=key)


@router.post("/cases/{case_id}/manager-decision", response_model=CaseResponse)
def manager_decision(case_id: UUID, body: Decision, session: SessionContext = Depends(retail_session),
                     key: str = Header(alias="Idempotency-Key"), service=Depends(case_service)):
    return invoke(service.decide, str(case_id), session=session, role="retail-manager", key=key, **body.model_dump())


@router.post("/cases/{case_id}/supplier-decision", response_model=CaseResponse)
def supplier_decision(case_id: UUID, body: Decision, session: SessionContext = Depends(current_session),
                      key: str = Header(alias="Idempotency-Key"), service=Depends(case_service)):
    return invoke(service.decide, str(case_id), session=session, role="supplier", key=key, **body.model_dump())


@router.post("/cases/{case_id}/dispatch/{kind}", response_model=CaseResponse | DispatchPending)
def dispatch(case_id: UUID, kind: Literal["request", "response"], session: SessionContext = Depends(current_session),
             service=Depends(case_service)):
    return invoke(service.dispatch, str(case_id), session=session,
                  kind="SUPPLIER_REQUEST" if kind == "request" else "SUPPLIER_RESPONSE")


@router.post("/cases/{case_id}/complete", response_model=CaseResponse)
def complete(case_id: UUID, session: SessionContext = Depends(current_session),
             key: str = Header(alias="Idempotency-Key"), service=Depends(case_service)):
    return invoke(service.close_case, str(case_id), session=session, key=key)


@router.get("/items/{item_id}/suppliers", response_model=ComparisonResponse)
def compare(item_id: ItemId, quantity: int = Query(ge=1, le=1_000_000), expedited: bool = False,
            session: SessionContext = Depends(retail_session)):
    return invoke(get_container().supplier_service.compare, item_id, quantity, expedited=expedited)


@router.get("/supplier/items/{item_id}/quote", response_model=QuoteResponse)
def own_quote(item_id: ItemId, quantity: int = Query(ge=1, le=1_000_000), expedited: bool = False,
              session: SessionContext = Depends(current_session)):
    if session.role != "supplier":
        raise HTTPException(403, "Supplier session required")
    return invoke(get_container().supplier_service.calculate_quote, session.supplier_id, item_id, quantity,
                  expedited=expedited, session=session)


@router.get("/cases", response_model=CasePage)
def list_cases(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=10000),
               session: SessionContext = Depends(current_session), service=Depends(case_service)):
    return invoke(service.list_cases, session=session, limit=limit, offset=offset)
