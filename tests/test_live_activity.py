"""Activity must be observable before completion, isolated and non-invasive."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from langgraph.errors import GraphInterrupt

from app.activity_api import router
from app.live_activity import (ActivityMiddleware, ActivityStore, RequestActivity,
    active_activity, activity_span, activity_note, store)
from app.session_api import current_session, get_session_service
from app.services.sessions import SessionContext, SessionError
from app.proxy_routes import integrated_target, session_headers


@pytest.fixture
def harness():
    owners = {key: SessionContext(session_id=uuid4(), role='retail-manager',
        expires_at=datetime.now(timezone.utc)+timedelta(hours=1)) for key in ('one', 'two')}
    def resolve(header):
        if header not in owners:
            raise SessionError('Bearer session required')
        return owners[header]
    app = FastAPI()
    app.add_middleware(ActivityMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_session_service] = lambda: SimpleNamespace(from_authorization=resolve)
    entered, release = Event(), Event()
    @app.post('/chat')
    def chat(session=Depends(current_session)):
        with activity_span('model', 'Generating response'):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test synchronization failed')
            activity_note('built_in_tool', 'file_search', 'completed')
        return {'message': 'fixture answer'}
    @app.post('/retail/workflows')
    def workflow(session=Depends(current_session)):
        try:
            with activity_span('graph', 'retail.select'):
                raise GraphInterrupt(())
        except GraphInterrupt:
            return {'status': 'awaiting_selection'}
    @app.post('/supplier/workflows')
    def failed(session=Depends(current_session)):
        with activity_span('tool', 'get_supplier_policy'):
            raise ValueError('private exception must not appear in activity')
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, entered, release
    release.set()


def test_live_running_then_completed_and_session_isolation(harness):
    client, entered, release = harness
    identifier = str(uuid4())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, '/chat', headers={'Authorization': 'one', 'X-Activity-ID': identifier})
        try:
            assert entered.wait(3)
            live = client.get('/activity/'+identifier, headers={'Authorization': 'one'})
            assert live.headers['cache-control'] == 'no-store'
            assert live.json()['status'] == 'running'
            assert live.json()['steps'][0]['status'] == 'running'
            assert client.get('/activity/'+identifier, headers={'Authorization': 'two'}).status_code == 404
            assert client.get('/activity/'+identifier).status_code == 401
        finally:
            release.set()
        assert future.result().json() == {'message': 'fixture answer'}
    final = client.get('/activity/'+identifier, headers={'Authorization': 'one'}).json()
    assert final['status'] == 'completed'
    assert final['steps'][0]['duration_ms'] >= 0
    assert final['steps'][1]['name'] == 'file_search'
    assert final['steps'][1]['parent_id'] == final['steps'][0]['id']
    assert 'fixture answer' not in str(final)
    assert '_owner' not in final


def test_pause_is_not_a_failure_and_failed_step_is_redacted(harness):
    client, _, _ = harness
    for route, outcome, step in [('/retail/workflows', 'completed', 'waiting'), ('/supplier/workflows', 'failed', 'failed')]:
        identifier = str(uuid4())
        client.post(route, headers={'Authorization': 'one', 'X-Activity-ID': identifier})
        data = client.get('/activity/'+identifier, headers={'Authorization': 'one'}).json()
        assert data['status'] == outcome
        assert data['steps'][0]['status'] == step
        assert 'private exception' not in str(data)


def test_missing_or_invalid_activity_header_does_not_change_operation(harness):
    client, _, release = harness
    release.set()
    for header in ({}, {'X-Activity-ID': 'invalid'}):
        assert client.post('/chat', headers={'Authorization': 'one', **header}).json() == {'message': 'fixture answer'}


def test_bounded_store_duplicate_ids_and_expiry():
    data = ActivityStore(capacity=1, max_steps=1)
    assert data.start('id', 'owner')
    assert not data.start('id', 'other')
    assert not data.start('second', 'owner')
    data.step('id', 'model', 'Generating response')
    assert data.step('id', 'tool', 'tool') is None
    assert data.read('id', 'owner')['truncated']
    assert data.read('id', 'other') is None
    data.rows['id']['_started'] -= 601
    assert data.read('id', 'owner') is None
    assert data.start('second', 'owner')


def test_context_cleanup_and_proxy_contract():
    identifier = str(uuid4())
    request = RequestActivity(identifier)
    request.bind(SimpleNamespace(session_id='owner'))
    token = active_activity.set(request)
    try:
        with pytest.raises(ValueError):
            with activity_span('tool', 'fixed tool name'):
                raise ValueError('private input')
        with activity_span('database', 'Reading Oracle data'):
            pass
    finally:
        active_activity.reset(token)
    steps = store.read(identifier, 'owner')['steps']
    assert [step['status'] for step in steps] == ['failed', 'completed']
    assert all(step['parent_id'] is None for step in steps)
    assert integrated_target('GET', 'activity/'+identifier, '') == 'activity/'+identifier
    assert session_headers({'x-activity-id': identifier}) == {'X-Activity-ID': identifier}
