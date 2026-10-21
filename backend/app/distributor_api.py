"""Chat-only Distributor boundary in the existing Retail backend process."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StringConstraints

from app.dependencies import get_container
from app.distributor_rag.domain.errors import AppError
from app.session_api import authenticated_session
from app.services.sessions import SessionContext


def distributor_session(session: SessionContext = Depends(authenticated_session)):
    if session.role != 'distributor':
        raise HTTPException(403, 'Distributor session required')
    return session


router = APIRouter(prefix='/distributor', tags=['Distributor chat'],
                   dependencies=[Depends(distributor_session)])


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


def service():
    return get_container().distributor_service


def invoke(operation, *args):
    try:
        return operation(*args)
    except AppError as error:
        raise HTTPException(error.status_code, error.message) from None
    except Exception:
        # No SQL, connection strings, configuration values or SDK bodies in responses.
        raise HTTPException(503, 'Distributor service unavailable. Ask the administrator to check its configuration and knowledge readiness.') from None


@router.get('/knowledge')
def knowledge(response: Response, agent=Depends(service)):
    response.headers['Cache-Control'] = 'no-store'
    return invoke(agent.knowledge)


@router.post('/questions/ask')
def ask(body: Question, response: Response, agent=Depends(service)):
    response.headers['Cache-Control'] = 'no-store'
    return invoke(agent.ask, body.question)
