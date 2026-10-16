"""Uniform MCP validation, trusted context, safety and redacted failures."""
import asyncio
from contextvars import ContextVar
from functools import wraps
from inspect import signature, Parameter
import logging
from time import perf_counter
from uuid import uuid4

from fastmcp.exceptions import ToolError
from pydantic import ConfigDict, TypeAdapter, ValidationError, create_model
from app.enterprise_ai.mcp_safety import CONTROLLED_TOOLS, RETAIL_TOOLS, SUPPLIER_TOOLS
from app.observability.context import request_trace_id, TraceContext
from app.observability.langfuse import LangfuseObservabilityClient
from app.services.cases import CaseService, CaseConflict, CaseNotFound
from app.services.inventory import ItemNotFound
from app.services.policy import PolicyDocumentError
from app.services.sessions import SessionError

logger = logging.getLogger(__name__)
tool_session = ContextVar("mcp_tool_session", default=None)
tool_trace = ContextVar("mcp_tool_trace", default=None)


def safe_error(exc, trace_id):
    if isinstance(exc, SessionError):
        code = "unauthorized"
    elif isinstance(exc, PermissionError):
        code = "forbidden"
    elif isinstance(exc, (CaseNotFound, ItemNotFound)):
        code = "not_found"
    elif isinstance(exc, CaseConflict):
        code = "conflict"
    elif isinstance(exc, PolicyDocumentError):
        code = "policy_unavailable"
    elif isinstance(exc, NotImplementedError):
        code = "capability_unavailable"
    elif isinstance(exc, (ValidationError, ValueError, TypeError)):
        code = "validation_error"
    else:
        code = "dependency_unavailable"
    return ToolError(f"{code}; trace_id={trace_id}. No approval is inferred; retain the same action key for retries.")


def record_control(stage, status, tool, error_code=None):
    """Fixed operational metadata only; no arguments, results or exception text."""
    from app.chat_telemetry import event, active_trace
    event(stage, status, tool=tool, api_source='local_mcp', error_code=error_code)
    if active_trace.get() is None:
        logger.info('mcp_control_check', extra={'stage': stage, 'status': status,
            'mcp_tool': tool, 'api_source': 'local_mcp', 'error_code': error_code})


def secured_tool(container_getter, session_getter):
    def decorate(fn):
        sig = signature(fn)
        inputs = create_model(fn.__name__ + "Input", __config__=ConfigDict(extra="forbid"), **{
            name: (p.annotation, ... if p.default is Parameter.empty else p.default)
            for name, p in sig.parameters.items()})
        output = TypeAdapter(sig.return_annotation)

        @wraps(fn)
        def execute(*args, **kwargs):
            trace_id = request_trace_id.get() or str(uuid4())
            trace_token = request_trace_id.set(trace_id)
            session_token = context_token = None
            started, status = perf_counter(), "error"
            metadata = {"trace_id": trace_id}
            check, denial = "session", None
            try:
                session = CaseService._session(session_getter())
                check = "arguments"
                bound = sig.bind(*args, **kwargs)
                values = inputs.model_validate(bound.arguments).model_dump()
                check = "permissions"
                if fn.__name__ in RETAIL_TOOLS and fn.__name__ not in SUPPLIER_TOOLS:
                    session.require_role("retail-manager")
                elif fn.__name__ in SUPPLIER_TOOLS and fn.__name__ not in RETAIL_TOOLS:
                    session.require_role("supplier")
                if session.role == "supplier" and "supplier_id" in values:
                    session.require_supplier(values["supplier_id"])
                trace = TraceContext(trace_id=trace_id, session_id=str(session.session_id),
                    role=session.role, supplier_id=session.supplier_id, mcp_tool=fn.__name__,
                    item_id=values.get("item_id"), case_id=values.get("case_id"))
                session_token = tool_session.set(session)
                context_token = tool_trace.set(trace)
                metadata = trace.as_metadata()
                check = "dependency"
                container = container_getter()
                context = trace.as_metadata()
                tracing = getattr(container, "langfuse", None) or LangfuseObservabilityClient()
                with tracing.observation(
                    "mcp." + fn.__name__, as_type="tool", input=values,
                    metadata={"workflow": "mcp", "tool_name": fn.__name__}, context=trace,
                ) as observation:
                    if fn.__name__ in CONTROLLED_TOOLS:
                        check = "approval"
                        context.update(container.case_service.approval_context(
                            values["case_id"], session=session, tool_name=fn.__name__))
                    check = "policy"
                    verdict = asyncio.run(container.mcp_safety.validate_tool_call(fn.__name__, context))
                    if verdict.get("allowed") is not True:
                        denial = 'human_approval_required' if verdict.get('reason') == 'human_approval_required' else 'local_policy_denied'
                        raise PermissionError("Safety denied")
                    record_control('mcp_safety', 'passed', fn.__name__)
                    check = "execution"
                    result = fn(**values)
                    check = "result"
                    try:
                        result = output.validate_python(result)
                    except Exception:
                        raise RuntimeError("Invalid service result") from None
                    record_control('mcp_result', 'passed', fn.__name__)
                    observation.update(output=result, metadata={"status": "success"})
                status = "ok"
                return result
            except Exception as exc:
                # A service outage is not a failed safety check. Output validation
                # is recorded separately from the pre-execution permission gate.
                if check not in {'execution', 'dependency'}:
                    code = denial or {'session': 'session_required', 'arguments': 'invalid_arguments',
                        'permissions': 'permission_denied', 'approval': 'approval_evidence_unavailable',
                        'policy': 'policy_check_unavailable', 'result': 'invalid_tool_result'}[check]
                    outcome = 'blocked' if isinstance(exc, (PermissionError, ValueError, TypeError)) and check != 'result' else 'failed'
                    record_control('mcp_result' if check == 'result' else 'mcp_safety', outcome, fn.__name__, code)
                logger.warning('mcp_tool_failed', extra={**metadata, 'mcp_tool': fn.__name__,
                    'error_type': type(exc).__name__, 'status': 'failed'}, exc_info=True)
                raise safe_error(exc, trace_id) from None
            finally:
                logger.info("MCP tool=%s status=%s duration_ms=%.2f", fn.__name__, status,
                    (perf_counter()-started)*1000, extra=metadata)
                if context_token is not None:
                    tool_trace.reset(context_token)
                if session_token is not None:
                    tool_session.reset(session_token)
                request_trace_id.reset(trace_token)

        execute.input_model = inputs
        return execute
    return decorate
