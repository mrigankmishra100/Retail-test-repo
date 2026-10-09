"""Supplier workflow controls; no client graph state, role or approval payloads."""
from uuid import UUID
from fastapi import APIRouter, Depends, Header
from app.agents.contracts import StartSupplierWorkflow, ResumeSupplierWorkflow, SupplierWorkflowResponse
from app.dependencies import get_container
from app.session_api import supplier_session
from app.services.sessions import SessionContext
from app.case_api import invoke

router = APIRouter(prefix="/supplier/workflows", tags=["Supplier workflow"])


def workflow_service():
    return get_container().supplier_workflow_service


@router.post("", response_model=SupplierWorkflowResponse, status_code=201)
def start(body: StartSupplierWorkflow, session: SessionContext = Depends(supplier_session),
          key: str = Header(alias="Idempotency-Key"), service=Depends(workflow_service)):
    return invoke(service.start, body, session=session, key=key)


@router.get("/{thread_id}", response_model=SupplierWorkflowResponse)
def get(thread_id: UUID, session: SessionContext = Depends(supplier_session), service=Depends(workflow_service)):
    return invoke(service.get, str(thread_id), session=session)


@router.post("/{thread_id}/resume", response_model=SupplierWorkflowResponse)
def resume(thread_id: UUID, body: ResumeSupplierWorkflow | None = None, session: SessionContext = Depends(supplier_session),
           service=Depends(workflow_service)):
    # Missing/empty input is a resume signal, never a supplier decision.
    # Keep strict rejection of extra fields and all service-level approval gates.
    return invoke(service.resume, str(thread_id), session=session)
