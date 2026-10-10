"""Session-authenticated registry management for both workspace roles."""

from uuid import UUID
import logging
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_container
from app.session_api import current_session
from app.services.agent_registry import (
    AgentPage, AgentRegistration, RegisteredAgent, RegistryConflict, RegistryNotFound,
)

router = APIRouter(prefix="/registry/agents", tags=["Agent registry"],
                   dependencies=[Depends(current_session)])
logger = logging.getLogger(__name__)


def registry_service():
    return get_container().agent_registry_service


def invoke(action, *args, **kwargs):
    started, status = monotonic(), 'failed'
    name = getattr(action, '__name__', None)
    operation = name if name in {'list_agents', 'create', 'set_active'} else 'registry_operation'
    try:
        result = action(*args, **kwargs)
        status = 'success'
        return result
    except RegistryNotFound as exc:
        status = 'not_found'
        raise HTTPException(404, "Agent not found") from exc
    except RegistryConflict as exc:
        status = 'conflict'
        raise HTTPException(409, "Deactivate the current agent of this type first") from exc
    except Exception as exc:
        logger.warning('agent_registry_failed', extra={'stage': operation,
            'error_type': type(exc).__name__}, exc_info=True)
        raise HTTPException(503, "Agent registry unavailable; check database and migration 004") from exc
    finally:
        logger.info('agent_registry_completed', extra={'stage': operation, 'status': status,
            'duration_ms': round((monotonic()-started)*1000, 2)})


@router.get("", response_model=AgentPage)
def list_agents(limit: int = Query(default=20, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=10000), service=Depends(registry_service)):
    return invoke(service.list_agents, limit=limit, offset=offset)


@router.post("", response_model=RegisteredAgent, status_code=201)
def create_agent(body: AgentRegistration, service=Depends(registry_service)):
    return invoke(service.create, body)


@router.post("/{agent_id}/activate", response_model=RegisteredAgent)
def activate_agent(agent_id: UUID, service=Depends(registry_service)):
    return invoke(service.set_active, agent_id, True)


@router.post("/{agent_id}/deactivate", response_model=RegisteredAgent)
def deactivate_agent(agent_id: UUID, service=Depends(registry_service)):
    return invoke(service.set_active, agent_id, False)
