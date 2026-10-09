"""Uniform public errors and server-generated request correlation, without payload logging."""
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from app.contracts import ErrorResponse
from app.observability.context import request_trace_id
from app.utils.safe_diagnostics import validation_details
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)
MESSAGES = {
    400: ("bad_request", "Invalid request"),
    401: ("unauthorized", "A valid bearer session is required"),
    403: ("forbidden", "This session is not allowed to perform that action"),
    404: ("not_found", "The requested resource was not found"),
    405: ("method_not_allowed", "Method not allowed"),
    409: ("conflict", "The action conflicts with the current state or an earlier request; refresh and review"),
    422: ("validation_error", "Request validation failed"),
    500: ("internal_error", "An unexpected error occurred"),
    501: ("not_implemented", "This legacy or future operation is not available"),
    503: ("dependency_unavailable", "A required service is unavailable; retain the same idempotency key for a retry"),
}


def error_response(request, status, *, fields=None, headers=None, detail=None):
    code, message = MESSAGES.get(status, ("http_error", "Request could not be completed"))
    safe_details = {"policy_unavailable": "The required supplier policy is missing, invalid or unsupported",
                    "publishing_disabled": "Notification publishing is disabled; the approved intent remains queued"}
    if status == 503 and isinstance(detail, dict) and detail.get("code") in safe_details:
        code = detail["code"]
        message = safe_details[code]
    return JSONResponse(status_code=status, headers=headers, content={"error": {
        "code": code, "message": message, "trace_id": request.state.trace_id, "fields": fields or []}})


def install_http_contract(app: FastAPI):
    @app.middleware("http")
    async def correlate(request: Request, call_next):
        trace = str(uuid4())  # Never log or adopt a caller-supplied trace ID.
        request.state.trace_id = trace
        token = request_trace_id.set(trace)
        started = perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                logger.error("Unhandled request failure type=%s", type(exc).__name__, extra={"trace_id": trace})
                response = error_response(request, 500)
            response.headers["X-Trace-ID"] = trace
            response.headers["X-API-Version"] = "1"
            response.headers["Cache-Control"] = "no-store"
            route = request.scope.get("route")
            logger.info("HTTP %s %s status=%s", request.method, getattr(route, "path", "<unmatched>"),
                        response.status_code, extra={"trace_id": trace, "duration_ms": round((perf_counter()-started)*1000, 2)})
            return response
        finally:
            request_trace_id.reset(token)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, headers=exc.headers, detail=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never return pydantic input/ctx/msg; they can contain submitted secrets.
        fields = [{"location": list(e["loc"]) if e["type"] != "extra_forbidden" else ["body"],
                   "code": e["type"]} for e in exc.errors()]
        return error_response(request, 422, fields=fields)

    @app.exception_handler(ResponseValidationError)
    async def response_validation_error(request, exc):
        route = request.scope.get('route')
        schema = {}
        try:
            schema = TypeAdapter(route.response_model).json_schema()
        except Exception:
            pass  # Unknown fields remain redacted if schema introspection fails.
        logger.error('response_validation_failed', extra={
            'trace_id':request.state.trace_id, 'stage':'response_validation',
            'error_type':'ResponseValidationError',
            **validation_details(exc.errors(), schema)})
        return error_response(request, 500)


ERROR_RESPONSES = {code: {"model": ErrorResponse, "description": message}
                   for code, (_, message) in MESSAGES.items()}
