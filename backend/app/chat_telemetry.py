"""Per-invocation telemetry: safe operational events, provider usage and explicit cost coverage."""
from contextvars import ContextVar
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4
import logging
import re

active_trace = ContextVar('chat_trace', default=None)
logger = logging.getLogger(__name__)


def event(stage, status, *, tool=None, duration_ms=None, error_code=None, api_source=None):
    if stage in {'agent_mapping', 'tool_transport', 'retrieval', 'built_in_tool', 'chat', 'registry', 'model_tool_request'}:
        from app.live_activity import activity_note
        activity_note(stage, tool or stage, status, api_source=api_source)
    trace = active_trace.get()
    if trace is not None:
        row = {'time': datetime.now(timezone.utc).isoformat(), 'stage': stage, 'status': status}
        if tool is not None:
            row['tool'] = tool
        if duration_ms is not None:
            row['duration_ms'] = round(duration_ms, 2)
        if error_code is not None:
            row['error_code'] = error_code
        if api_source is not None:
            row['api_source'] = api_source
        trace.events.append(row)
        logger.info('chat_execution_event', extra={'trace_id': trace.id, 'stage': stage,
            'status': status, 'mcp_tool': tool, 'duration_ms': duration_ms,
            'model_id': trace.profile.model_id, 'api_source': api_source})


def integer(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(raw):
    raw = raw if isinstance(raw, dict) else {}
    incoming = raw.get('input_tokens_details') if isinstance(raw.get('input_tokens_details'), dict) else {}
    outgoing = raw.get('output_tokens_details') if isinstance(raw.get('output_tokens_details'), dict) else {}
    return {'input_tokens': integer(raw.get('input_tokens')), 'output_tokens': integer(raw.get('output_tokens')),
        'cached_tokens': integer(incoming.get('cached_tokens')), 'cache_write_tokens': integer(incoming.get('cache_write_tokens')),
        'reasoning_tokens': integer(outgoing.get('reasoning_tokens'))}


class ChatTrace:
    def __init__(self, profile):
        self.id, self.profile, self.started = str(uuid4()), profile, monotonic()
        self.calls, self.events, self.citations = [], [], []
        self.security_checks, self.queries = [], []

    def cost(self, usage, file_calls):
        p = self.profile
        inp, out = usage['input_tokens'], usage['output_tokens']
        if inp is None or out is None or p.input_usd_per_million is None or p.output_usd_per_million is None:
            return None
        cached, written = usage['cached_tokens'] or 0, usage['cache_write_tokens'] or 0
        if cached + written > inp:
            return None
        if cached and p.cached_input_usd_per_million is None or written and p.cache_write_usd_per_million is None:
            return None
        if file_calls and p.file_search_usd_per_call is None:
            return None
        return round(((inp-cached-written)*p.input_usd_per_million + out*p.output_usd_per_million
            + cached*(p.cached_input_usd_per_million or 0) + written*(p.cache_write_usd_per_million or 0))/1_000_000
            + file_calls*(p.file_search_usd_per_call or 0), 8)

    def record(self, result, elapsed, *, error=False):
        usage = normalize_usage(result.get('usage'))
        builtins = result.get('builtin_tools', [])
        file_calls = sum(t.get('type') == 'file_search_call' for t in builtins)
        row = {'index': len(self.calls)+1, 'status': 'failed' if error else result.get('status', 'unknown'),
            'error_code': result.get('error_code'), 'http_status': result.get('http_status'),
            'model_id': result.get('model_id', self.profile.model_id), 'latency_ms': round(elapsed, 2),
            **usage, 'file_search_calls': file_calls, 'cost_usd': self.cost(usage, file_calls),
            'model_cost_usd': self.cost(usage, 0)}
        self.calls.append(row)
        self.citations.extend(result.get('citations', []))
        event('model', row['status'], duration_ms=elapsed)
        for tool in builtins:
            event('built_in_tool', tool.get('status', 'unknown'), tool='file_search')

    def finish(self, status, *, route='local', quality=None):
        sums = {}
        for field in ('input_tokens', 'output_tokens', 'cached_tokens', 'cache_write_tokens', 'reasoning_tokens', 'cost_usd', 'model_cost_usd'):
            values = [call[field] for call in self.calls]
            sums[field] = round(sum(values), 8) if values and all(v is not None for v in values) else None
        return {'run_id': self.id, 'profile_id': self.profile.id, 'model_id': self.profile.model_id,
            'provider': self.profile.provider,
            'status': status, 'route': route, 'duration_ms': round((monotonic()-self.started)*1000, 2),
            'model_latency_ms': round(sum(c['latency_ms'] for c in self.calls), 2),
            'call_count': len(self.calls), 'calls': self.calls, 'events': self.events,
            'security_checks': self.security_checks[:40], 'queries': self.queries[:8],
            'citations': self.citations[:30], 'price_version': self.profile.price_version,
            'estimated_rates': {name: getattr(self.profile, name) for name in (
                'input_usd_per_million', 'output_usd_per_million', 'cached_input_usd_per_million',
                'cache_write_usd_per_million')},
            'model_cost_scope': 'Estimated model tokens only; excludes File Search, guardrails, storage, database and hosting. Not an OCI bill.',
            'cost_scope': 'Model tokens and reported File Search calls; excludes storage, database and hosting',
            'usage_complete': bool(self.calls) and all(c['input_tokens'] is not None and c['output_tokens'] is not None for c in self.calls),
            'quality': quality or {}, **sums}


class MeasuredResponses:
    def __init__(self, client, trace):
        self.client, self.trace = client, trace

    def __getattr__(self, name):
        return getattr(self.client, name)

    def create_response_sync(self, *args, **kwargs):
        started = monotonic()
        try:
            result = self.client.create_response_sync(*args, **kwargs)
        except Exception as exc:
            # A provider may have returned usage before response validation failed.
            raw = {**getattr(exc, 'chat_result', {}), 'error_code': getattr(exc, 'public_code', 'model_request_failed'),
                   'http_status': getattr(exc, 'http_status', None)}
            self.trace.record(raw, (monotonic()-started)*1000, error=True)
            raise
        self.trace.record(result, (monotonic()-started)*1000)
        provider = getattr(self.client, 'provider', None)
        source = {'gemini': 'gemini', 'openai': 'openai_responses', 'anthropic': 'anthropic'}.get(provider)
        if source is None:
            source = {'responses': 'oci_responses', 'native_chat': 'oci_chat'}.get(getattr(self.client, 'api_mode', None), 'model_api')
        # Record requests separately from executions. A requested tool can still
        # be rejected by argument validation, guardrails or permission checks.
        definitions = kwargs.get('tools', args[2] if len(args) > 2 else None) or []
        offered = {definition.get('name') for definition in definitions if isinstance(definition, dict)}
        calls = result.get('tool_calls')
        for call in calls[:32] if isinstance(calls, list) else []:
            name = call.get('name') if isinstance(call, dict) else None
            if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name):
                event('model_tool_request', 'requested', tool=name if name in offered else 'unavailable_tool', api_source=source)
        return result
