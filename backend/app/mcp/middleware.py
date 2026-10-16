"""Validate before SDK execution so raw invalid arguments cannot enter SDK error logs."""
from uuid import uuid4, UUID
import logging
from time import monotonic
from fastmcp.server.middleware import Middleware
from fastmcp.exceptions import ToolError
from app.mcp import tools
from app.mcp.boundary import safe_error
from app.observability.context import request_trace_id

logger = logging.getLogger(__name__)


class ToolBoundaryMiddleware(Middleware):
    def __init__(self):
        from app.agent_app import ReplayGuard
        self.delegation_replay = ReplayGuard()

    async def on_call_tool(self, context, call_next):
        trace_id = request_trace_id.get() or str(uuid4())
        token = request_trace_id.set(trace_id)
        delegated_token = model_token = None
        started, stage, status = monotonic(), 'input_validation', 'failed'
        metadata = {'trace_id':trace_id, 'mcp_tool':'<unknown>'}
        try:
            name = context.message.name
            if name not in tools.TOOL_FUNCTIONS:
                await tools.get_container().mcp_safety.validate_tool_call(name)
                raise ToolError(f"unknown_tool; trace_id={trace_id}")
            metadata['mcp_tool'] = name
            # Reject extras, forged approval context and invalid values before FastMCP validation.
            tools.TOOL_FUNCTIONS[name].input_model.model_validate(context.message.arguments or {})
            from fastmcp.server.dependencies import get_http_headers
            from app.mcp.remote import delegated_session, verify_assertion
            headers = get_http_headers(include={'x-mcp-assertion','x-mcp-call-id'})
            if 'x-mcp-assertion' in headers:
                stage = 'delegation_verification'
                arguments = tools.TOOL_FUNCTIONS[name].input_model.model_validate(
                    context.message.arguments or {}).model_dump(mode='json')
                session, model_binding = verify_assertion(headers['x-mcp-assertion'],
                    tools.get_container().settings.remote_mcp_service_keys,
                    name, arguments, self.delegation_replay, include_model=True)
                delegated_token = delegated_session.set(session)
                from app.mcp.model_binding import MODEL_BOUND_TOOLS
                if model_binding and name in MODEL_BOUND_TOOLS:
                    from app.chat_models import profiles
                    from app.chat_telemetry import ChatTrace
                    from app.agents.tool_access import binding, RetailToolAccess
                    container = tools.get_container()
                    profile = profiles(container.settings).get(model_binding['profile'])
                    if profile is None or profile.model_id != model_binding['model_id']:
                        raise ValueError('Selected model is not configured on this worker')
                    scoped = container.chat_experience_service.scoped_container(profile, False, ChatTrace(profile), session)
                    model_token = binding.set(RetailToolAccess(scoped, session))
                # Correlation only, after verified delegation; never used as identity.
                try:
                    metadata['call_id'] = str(UUID(headers.get('x-mcp-call-id','')))
                except (ValueError, TypeError, AttributeError):
                    pass
            stage = 'tool_execution'
            if delegated_token is not None:
                from app.remote_activity import capture_activity, export_activity
                from fastmcp.tools.base import ToolResult
                with capture_activity(session) as activity:
                    try:
                        result = await call_next(context)
                    except Exception as exc:
                        # Preserve failure evidence without exposing raw exception text.
                        code = str(exc).split(';', 1)[0] if isinstance(exc, ToolError) else ''
                        if code not in {'not_found', 'conflict', 'forbidden', 'unauthorized',
                                        'validation_error', 'policy_unavailable', 'capability_unavailable'}:
                            code = 'dependency_unavailable'
                        result = ToolResult(content=code + '; MCP tool failed', is_error=True)
                    result.meta = {**(result.meta or {}), 'retail_activity': export_activity(activity)}
            else:
                result = await call_next(context)
            stage, status = 'completed', 'success'
            if getattr(result, 'is_error', False):
                status = 'failed'
            return result
        except ToolError as exc:
            logger.warning('mcp_boundary_failed', extra={**metadata,'stage':stage,'error_type':type(exc).__name__})
            raise
        except Exception as exc:
            logger.warning('mcp_boundary_failed', extra={**metadata,'stage':stage,'error_type':type(exc).__name__})
            raise safe_error(exc, trace_id) from None
        finally:
            logger.info('mcp_boundary_completed', extra={**metadata,'stage':stage,'status':status,
                'duration_ms':round((monotonic()-started)*1000,2)})
            if delegated_token is not None:
                delegated_session.reset(delegated_token)
            if model_token is not None:
                binding.reset(model_token)
            request_trace_id.reset(token)
