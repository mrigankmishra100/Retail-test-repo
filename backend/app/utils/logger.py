"""Standard-library logging configuration shared across the backend."""

import logging
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from typing import Any
from app.observability.context import request_trace_id

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
CONTEXT_FIELDS = (
    "trace_id",
    "session_id",
    "conversation_id",
    "case_id",
    "item_id",
    "supplier_id",
    "role",
    "agent",
    "graph_node",
    "mcp_tool",
    "approval_type",
    "approval_result",
    "duration_ms",
    "status",
    "service",
    "method",
    "path",
    "request_id",
    "error_type",
    "error_codes",
    "db_stage",
    "db_operation",
    "stage", "source", "reason", "auth_mode", "http_status",
    "opc_request_id", "notification_id", "call_id", "setting_count",
    "not_delivered", "retryable",
    "protocol_error_code", "validation_errors", "error_count",
    "release_tag", "features",
)


class JsonFormatter(logging.Formatter):
    """One JSON event per line; context is restricted to known fields."""

    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            if hasattr(record, field):
                event[field] = getattr(record, field)
        event.setdefault('trace_id', request_trace_id.get() or '-')
        if record.exc_info:
            # Exception messages/locals can contain connection strings or payloads.
            event["error_type"] = record.exc_info[0].__name__
            event["stack"] = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(event, default=str)


def configure_logging(level: int | None = None) -> None:
    """Configure process-wide logging without third-party dependencies."""
    if level is None:
        level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
        if not isinstance(level, int):
            level = logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    # Uvicorn installs its own handler before importing the app. Use our redacting
    # formatter there too so an unhandled exception is not duplicated with secrets.
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    # SDK debug output may contain headers or payloads; app diagnostics are sufficient.
    for name in ('httpx', 'httpx2', 'httpcore', 'urllib3', 'oci'):
        logging.getLogger(name).setLevel(logging.WARNING)


def ensure_logging() -> None:
    """Enable redacted bootstrap logs before Settings is constructed."""
    if not any(isinstance(h.formatter, JsonFormatter) for h in logging.getLogger().handlers):
        configure_logging()


def provider_metadata(value) -> dict:
    """Return bounded status/request-ID metadata, never exception text or bodies."""
    response = getattr(value, 'response', None)
    status = getattr(value, 'status', None)
    if not isinstance(status, int):
        status = getattr(response, 'status_code', None)
    headers = getattr(value, 'headers', None)
    if not hasattr(headers, 'get'):
        headers = getattr(response, 'headers', {})
    request_id = headers.get('opc-request-id') if hasattr(headers, 'get') else None
    if request_id is None:
        request_id = getattr(value, 'request_id', None)
    return {
        'http_status': status if isinstance(status, int) and 100 <= status <= 599 else None,
        'opc_request_id': request_id if isinstance(request_id, str) and
            re.fullmatch(r'[A-Za-z0-9/_-]{1,256}', request_id) else None,
    }


def get_logger(name: str, **context: Any) -> logging.LoggerAdapter:
    """Return a logger adapter ready for future structured context fields."""
    return logging.LoggerAdapter(logging.getLogger(name), context)
