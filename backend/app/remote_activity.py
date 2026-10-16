"""Bounded operational activity carried over authenticated service responses.

No prompts, arguments, result bodies or exception text are accepted here. Each
worker captures into a request-private store; the caller retains session ownership.
Remote steps become visible on return, not as a cross-service live stream.
"""
from contextlib import contextmanager
from datetime import datetime
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from app.live_activity import ActivityStore, RequestActivity, active_activity, parent_step


class Step(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    parent_id: UUID | None = None
    stage: str = Field(pattern=r'^[a-z_]{1,40}$')
    name: str = Field(pattern=r'^[A-Za-z0-9_.: /·-]{1,160}$')
    status: str = Field(pattern=r'^[A-Za-z_]{1,40}$')
    time: datetime
    offset_ms: float = Field(ge=0, le=3_600_000, allow_inf_nan=False)
    duration_ms: float | None = Field(default=None, ge=0, le=3_600_000, allow_inf_nan=False)
    api_source: str | None = Field(default=None, pattern=r'^[a-z_]{1,40}$')
    error_code: str | None = Field(default=None, pattern=r'^[a-z_]{1,80}$')
    service: Literal['retail-agent', 'supplier-agent', 'mcp-server'] | None = None


class Activity(BaseModel):
    model_config = ConfigDict(extra='forbid')
    steps: list[Step] = Field(max_length=200)
    truncated: bool = False


@contextmanager
def capture_activity(session):
    activity = RequestActivity(str(uuid4()), ActivityStore(capacity=1))
    activity.bind(session)
    token = active_activity.set(activity)
    parent = parent_step.set(None)
    try:
        yield activity
    finally:
        parent_step.reset(parent)
        active_activity.reset(token)


def export_activity(activity):
    # Validation is best-effort telemetry, never a reason to fail a business call.
    with activity.store.lock:
        row = activity.store.rows.get(activity.id)
        if row is None:
            return {'steps': [], 'truncated': True}
        entries = [dict(entry) for entry in row['steps'][:200]]
        truncated = row['truncated'] or len(row['steps']) > 200
    steps = []
    for entry in entries:
        try:
            steps.append(Step.model_validate(entry))
        except (ValueError, TypeError):
            truncated = True
    return Activity(steps=steps, truncated=truncated).model_dump(mode='json', exclude_none=True)


def merge_activity(value, *, service, started):
    activity = active_activity.get()
    if value is None:
        return
    try:
        received = Activity.model_validate(value)
    except (ValueError, TypeError):
        return
    if service == 'mcp-server':
        # The MCP process has no caller ChatTrace. Return its real control results
        # to the invocation's durable metrics as well as its activity timeline.
        from app.chat_telemetry import active_trace
        trace = active_trace.get()
        if trace is not None:
            for step in received.steps:
                if step.stage in {'mcp_safety', 'mcp_result'} and len(trace.events) < 400:
                    trace.events.append({'time': step.time.isoformat(), 'stage': step.stage,
                        'status': step.status, 'tool': step.name, 'api_source': 'local_mcp',
                        **({'error_code': step.error_code} if step.error_code else {})})
    if not activity or not activity.bound:
        return
    with activity.store.lock:
        row = activity.store.rows.get(activity.id)
        if not row or row['status'] != 'running':
            return
        ids = {str(step.id): str(uuid4()) for step in received.steps}
        base = max(0, (started - row['_started']) * 1000)
        for step in received.steps:
            if len(row['steps']) >= activity.store.max_steps:
                row['truncated'] = True
                break
            entry = step.model_dump(mode='json', exclude_none=True)
            entry.update(id=ids[str(step.id)], parent_id=ids.get(str(step.parent_id), parent_step.get()),
                         offset_ms=round(base + step.offset_ms, 2), service=step.service or service)
            row['steps'].append(entry)
        row['truncated'] |= received.truncated
