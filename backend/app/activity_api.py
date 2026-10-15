"""Read live execution metadata for the authenticated session only."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Response
from app.session_api import current_session
from app.live_activity import store

router = APIRouter(prefix='/activity', tags=['Execution activity'])


@router.get('/{identifier}')
def activity(identifier: UUID, response: Response, session=Depends(current_session)):
    response.headers['Cache-Control'] = 'no-store'
    result = store.read(str(identifier), session.session_id)
    if result is None:
        raise HTTPException(404, 'Activity not available for this session')
    return result
