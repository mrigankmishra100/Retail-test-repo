"""HTTP session bootstrap and trusted role dependencies for the development POC."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.dependencies import get_container
from app.oci.cache import CacheUnavailable
from app.services.sessions import SessionContext, SessionError, SessionService

router = APIRouter()


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["retail-manager", "supplier"]
    supplier_id: str | None = Field(default=None, pattern=r"^SUP[0-9]{3}$")

    @model_validator(mode="after")
    def check_role(self):
        if (self.role == "supplier") != (self.supplier_id is not None):
            raise ValueError("Only supplier sessions require a supplier_id")
        return self


class SessionResponse(BaseModel):
    session_id: UUID
    role: Literal["retail-manager", "supplier"]
    supplier_id: str | None
    expires_at: datetime
    session_token: str


def get_session_service() -> SessionService:
    return get_container().session_service


def current_session(authorization: str | None = Header(default=None),
                    service: SessionService = Depends(get_session_service)) -> SessionContext:
    try:
        return service.from_authorization(authorization)
    except SessionError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    except CacheUnavailable as exc:
        raise HTTPException(503, "Session store unavailable") from exc


def retail_session(session: SessionContext = Depends(current_session)) -> SessionContext:
    if session.role != "retail-manager":
        raise HTTPException(403, "Retail-manager session required")
    return session


def supplier_session(session: SessionContext = Depends(current_session)) -> SessionContext:
    if session.role != "supplier":
        raise HTTPException(403, "Supplier session required")
    return session


@router.post("/sessions", response_model=SessionResponse, status_code=201)
def create_session(request: SessionRequest, response: Response,
                   service: SessionService = Depends(get_session_service)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.create(request.role, request.supplier_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except CacheUnavailable as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    except Exception as exc:
        raise HTTPException(503, "Session creation unavailable") from exc


@router.get("/sessions/me", response_model=SessionContext)
def get_session(response: Response, session: SessionContext = Depends(current_session)) -> SessionContext:
    response.headers["Cache-Control"] = "no-store"
    return session


@router.delete("/sessions/me", status_code=204)
def delete_session(authorization: str = Header(), session: SessionContext = Depends(current_session),
                   service: SessionService = Depends(get_session_service)) -> Response:
    try:
        service.revoke(authorization.split()[1])
    except CacheUnavailable as exc:
        raise HTTPException(503, "Session store unavailable") from exc
    return Response(status_code=204)
