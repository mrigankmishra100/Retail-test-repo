"""Session-authenticated registry management for both workspace roles."""

from uuid import UUID
import logging
from time import monotonic
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.dependencies import get_container
from app.session_api import current_session
from app.services.agent_registry import (
    AgentPage, AgentRegistration, RegisteredAgent, RegistryConflict, RegistryNotFound, RegistryDeleteConflict,
)

router = APIRouter(prefix="/registry/agents", tags=["Agent registry"],
                   dependencies=[Depends(current_session)])
logger = logging.getLogger(__name__)


class DeliveryConnection(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    api_token: SecretStr = Field(default=SecretStr(''), max_length=4105)


def delivery_service():
    from app.services.delivery_agent import DeliveryAgentService
    return DeliveryAgentService(get_container())


@router.get('/delivery-connection')
def delivery_connection_status(response: Response, session=Depends(current_session), service=Depends(delivery_service)):
    response.headers['Cache-Control'] = 'no-store'
    if session.role != 'supplier':
        raise HTTPException(403, 'Supplier session required')
    try:
        return service.connection_status(session=session)
    except Exception:
        raise HTTPException(503, 'Delivery connection status unavailable') from None


@router.post('/{agent_id}/delivery-connection')
def connect_delivery(agent_id: UUID, body: DeliveryConnection, session=Depends(current_session),
                     service=Depends(delivery_service)):
    if session.role != 'supplier':
        raise HTTPException(403, 'Supplier session required')
    try:
        return service.connect(agent_id, body.api_token.get_secret_value(), session=session)
    except RegistryNotFound:
        raise HTTPException(404, 'Agent not found') from None
    except ValueError:
        raise HTTPException(422, 'Check the Delivery agent and API token format') from None
    except Exception:
        raise HTTPException(503, 'Delivery connection could not be saved') from None


@router.delete('/{agent_id}/delivery-connection')
def disconnect_delivery(agent_id: UUID, session=Depends(current_session), service=Depends(delivery_service)):
    if session.role != 'supplier':
        raise HTTPException(403, 'Supplier session required')
    try:
        return service.disconnect(agent_id, session=session)
    except RegistryNotFound:
        raise HTTPException(404, 'Agent not found') from None
    except Exception:
        raise HTTPException(503, 'Delivery connection could not be removed') from None


def registry_service():
    return get_container().agent_registry_service


def invoke(action, *args, **kwargs):
    started, status = monotonic(), 'failed'
    name = getattr(action, '__name__', None)
    operation = name if name in {'list_agents', 'create', 'set_active', 'delete'} else 'registry_operation'
    try:
        result = action(*args, **kwargs)
        status = 'success'
        return result
    except RegistryNotFound as exc:
        status = 'not_found'
        raise HTTPException(404, "Agent not found") from exc
    except RegistryDeleteConflict as exc:
        status = 'conflict'
        raise HTTPException(409, 'Deactivate this agent before deleting it') from exc
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
def create_agent(body: AgentRegistration, service=Depends(registry_service), session=Depends(current_session)):
    return invoke(service.create, body, session=session)


@router.post("/{agent_id}/activate", response_model=RegisteredAgent)
def activate_agent(agent_id: UUID, service=Depends(registry_service), session=Depends(current_session)):
    return invoke(service.set_active, agent_id, True, session=session)


@router.delete('/{agent_id}')
def delete_agent(agent_id: UUID, service=Depends(registry_service), session=Depends(current_session)):
    return invoke(service.delete, agent_id, session=session)


@router.post("/{agent_id}/deactivate", response_model=RegisteredAgent)
def deactivate_agent(agent_id: UUID, service=Depends(registry_service), session=Depends(current_session)):
    return invoke(service.set_active, agent_id, False, session=session)
