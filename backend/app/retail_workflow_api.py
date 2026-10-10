"""Explicit workflow controls; chat and request bodies cannot assert trusted approval."""
from uuid import UUID
from fastapi import APIRouter, Depends, Header
from app.agents.contracts import StartRetailWorkflow, RetailSelection, ResumeRetailWorkflow, RetailWorkflowResponse
from app.dependencies import get_container
from app.session_api import retail_session
from app.services.sessions import SessionContext
from app.case_api import invoke

router = APIRouter(prefix="/retail/workflows", tags=["Retail workflow"])


def workflow_service():
    return get_container().retail_workflow_service


@router.post("", response_model=RetailWorkflowResponse, status_code=201)
def start(body: StartRetailWorkflow | None = None, session: SessionContext = Depends(retail_session),
          key: str = Header(alias="Idempotency-Key"), service=Depends(workflow_service)):
    # Hosted gateways may omit an empty JSON object. Auto-assessment explicitly
    # accepts no filters; authentication and idempotency remain mandatory.
    return invoke(service.start, body or StartRetailWorkflow(), session=session, key=key)


@router.get("/{thread_id}", response_model=RetailWorkflowResponse)
def get(thread_id: UUID, session: SessionContext = Depends(retail_session), service=Depends(workflow_service)):
    return invoke(service.get, str(thread_id), session=session)


@router.post("/{thread_id}/selection", response_model=RetailWorkflowResponse)
def select(thread_id: UUID, body: RetailSelection, session: SessionContext = Depends(retail_session), service=Depends(workflow_service)):
    return invoke(service.select, str(thread_id), body, session=session)


@router.post("/{thread_id}/resume", response_model=RetailWorkflowResponse)
def resume(thread_id: UUID, body: ResumeRetailWorkflow | None = None,
           session: SessionContext = Depends(retail_session), service=Depends(workflow_service)):
    # Gateways may omit {}. The body carries no decisions; the service checks
    # persisted approvals and trusted session ownership before any side effect.
    return invoke(service.resume, str(thread_id), session=session)
