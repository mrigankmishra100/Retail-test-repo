"""Bounded, session-owned execution activity. Never stores inputs, outputs or credentials.

Process-local POC telemetry: polling must reach the same API worker. Missing telemetry
never retries or changes a business operation. Durable audit data lives elsewhere.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import RLock
from time import monotonic
from uuid import UUID, uuid4
import re

from starlette.middleware.base import BaseHTTPMiddleware

active_activity = ContextVar('live_activity', default=None)


class ActivityStore:
    def __init__(self, capacity=300, ttl=600, max_steps=200):
        self.capacity, self.ttl, self.max_steps = capacity, ttl, max_steps
        self.rows, self.lock = {}, RLock()

    def _prune(self):
        now = monotonic()
        self.rows = {key: row for key, row in self.rows.items() if now - row['_started'] < self.ttl}

    def start(self, identifier, owner):
        with self.lock:
            self._prune()
            # Never allow a caller-chosen ID to overwrite another request.
            if identifier in self.rows or len(self.rows) >= self.capacity:
                return False
            self.rows[identifier] = {'id': identifier, '_owner': str(owner), '_started': monotonic(),
                'status': 'running', 'steps': [], 'truncated': False}
            return True

    def step(self, identifier, stage, name, status='running', parent=None, api_source=None):
        with self.lock:
            row = self.rows.get(identifier)
            if row is None or row['status'] != 'running':
                return None
            if len(row['steps']) >= self.max_steps:
                row['truncated'] = True
                return None
            step_id = str(uuid4())
            row['steps'].append({'id': step_id, 'parent_id': parent, 'stage': stage,
                'name': str(name)[:160], 'status': str(status)[:40],
                'time': datetime.now(timezone.utc).isoformat(),
                'offset_ms': round((monotonic() - row['_started']) * 1000, 2)})
            if api_source:
                row['steps'][-1]['api_source'] = api_source
            return step_id

    def end_step(self, identifier, step_id, status, elapsed):
        with self.lock:
            row = self.rows.get(identifier)
            if row:
                for step in row['steps']:
                    if step['id'] == step_id:
                        step.update(status=status, duration_ms=round(elapsed * 1000, 2))
                        break

    def finish(self, identifier, status):
        with self.lock:
            row = self.rows.get(identifier)
            if row:
                row.update(status=status, duration_ms=round((monotonic()-row['_started'])*1000, 2))

    def read(self, identifier, owner):
        with self.lock:
            self._prune()
            row = self.rows.get(identifier)
            if row is None or row['_owner'] != str(owner):
                return None
            return {**{k: v for k, v in row.items() if not k.startswith('_') and k != 'steps'},
                'elapsed_ms': round((monotonic()-row['_started'])*1000, 2),
                'steps': [dict(step) for step in row['steps']]}


store = ActivityStore()
parent_step = ContextVar('live_activity_parent', default=None)


class RequestActivity:
    def __init__(self, identifier):
        self.id, self.bound = identifier, False

    def bind(self, session):
        if not self.bound:
            self.bound = store.start(self.id, session.session_id)


def bind_session(session):
    activity = active_activity.get()
    if activity:
        activity.bind(session)


@contextmanager
def activity_span(stage, name):
    activity = active_activity.get()
    if not activity or not activity.bound:
        yield
        return
    identifier = store.step(activity.id, stage, name, parent=parent_step.get())
    token, started, status = parent_step.set(identifier), monotonic(), 'completed'
    try:
        yield
    except BaseException as exc:
        # LangGraph uses a control-flow exception for human approval/selection.
        from langgraph.errors import GraphInterrupt
        status = 'waiting' if isinstance(exc, GraphInterrupt) else 'failed'
        raise
    finally:
        parent_step.reset(token)
        store.end_step(activity.id, identifier, status, monotonic()-started)


def activity_step(stage, name):
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            with activity_span(stage, name):
                return fn(*args, **kwargs)
        return run
    return decorate


def activity_note(stage, name, status, *, api_source=None):
    activity = active_activity.get()
    if activity and activity.bound:
        store.step(activity.id, stage, name, status, parent=parent_step.get(), api_source=api_source)


class ActivityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        identifier = request.headers.get('X-Activity-ID')
        eligible = (request.method == 'POST' and re.fullmatch(
            r'/(chat|(?:retail|supplier)/workflows(?:/[^/]+/(?:selection|resume|model))?|cases/[^/]+/(?:prepare|manager-decision|supplier-decision|complete|dispatch/(?:request|response)))', request.url.path))
        eligible = eligible or (request.method == 'GET' and re.fullmatch(r'/cases/[^/]+/supplier-email', request.url.path))
        try:
            identifier = str(UUID(identifier)) if identifier and eligible else None
        except (ValueError, TypeError):
            identifier = None
        if not identifier:
            return await call_next(request)
        activity = RequestActivity(identifier)
        token = active_activity.set(activity)
        status = 'failed'
        try:
            response = await call_next(request)
            status = 'completed' if response.status_code < 400 else 'failed'
            return response
        finally:
            if activity.bound:
                store.finish(identifier, status)
            active_activity.reset(token)
