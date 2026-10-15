"""Session-scoped chat model catalog, evaluations and operational telemetry."""
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from app.case_api import invoke
from app.dependencies import get_container
from app.session_api import retail_session
from app.services.chat_experience import EvaluationRequest
from app.services.cases import CaseNotFound
from app.external_models import ExternalModelInput, ExternalModelConnection, ExternalModelDiscoveryInput, ExternalModelOptions

router = APIRouter(prefix='/chat')

@router.get('/official-costs')
def official_costs(session=Depends(retail_session)):
    return get_container().official_costs.read()

@router.get('/central-logs')
def central_logs(hours: Literal['1', '6', '24'] = Query('1'), session=Depends(retail_session)):
    return get_container().central_logs.search(hours=int(hours))

@router.get('/routing')
def routing(session=Depends(retail_session)):
    container = get_container()
    try:
        agents = container.agent_registry_service.list_agents(limit=100).items
        return {'status': 'available', 'source': 'Live Oracle agent registry', 'agents': [
            {'name': agent.name, 'agent_type': agent.agent_type, 'active': agent.active,
             'routing': 'live_registry' if agent.active and agent.agent_type.lower() in container.settings.agent_endpoints
                        else 'local_execution' if agent.agent_type in ('RETAIL', 'SUPPLIER') else 'directory_only'}
            for agent in agents],
            'message': 'Remote agents resolve their active registry endpoint on every invocation. Local execution stays local until authenticated workers are configured.'}
    except Exception:
        return {'status': 'unavailable', 'agents': [], 'message': 'Registry could not be read; no unverified remote route is enabled.'}

@router.get('/models')
def models(session=Depends(retail_session)):
    return invoke(get_container().chat_experience_service.catalog, session=session)

@router.post('/external-models', response_model=ExternalModelConnection, status_code=201)
def external_model(body: ExternalModelInput, session=Depends(retail_session)):
    return invoke(get_container().chat_experience_service.connect_external, body, session=session)

@router.post('/external-model-options', response_model=ExternalModelOptions)
def external_model_options(body: ExternalModelDiscoveryInput, session=Depends(retail_session)):
    return invoke(get_container().chat_experience_service.discover_external, body, session=session)

@router.get('/metrics')
def metrics(session=Depends(retail_session)):
    return invoke(get_container().chat_experience_service.metrics, session=session)

@router.post('/evaluations', status_code=202)
def evaluate(body: EvaluationRequest, background: BackgroundTasks, session=Depends(retail_session)):
    service = get_container().chat_experience_service
    result = invoke(service.reserve_evaluation, body, session=session)
    background.add_task(service.evaluate, result['run_id'], body, session)
    return result

@router.get('/evaluations/{identifier}')
def evaluation(identifier: UUID, session=Depends(retail_session)):
    return invoke(get_container().chat_experience_service.get_evaluation, str(identifier), session=session)

class Rating(BaseModel):
    rating: int = Field(strict=True, ge=1, le=5)

@router.post('/runs/{identifier}/rating')
def rate(identifier: UUID, body: Rating, session=Depends(retail_session)):
    service = get_container().chat_experience_service
    def save():
        try:
            return service.store.rate(session.session_id, str(identifier), body.rating)
        except LookupError:
            raise CaseNotFound('Run not found') from None
    return invoke(save)
