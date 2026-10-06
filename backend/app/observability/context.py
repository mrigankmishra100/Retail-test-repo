"""Shared structured context for LangSmith and OCI observability."""

from dataclasses import asdict, dataclass
from typing import Any
from contextvars import ContextVar

request_trace_id: ContextVar[str | None] = ContextVar("request_trace_id", default=None)


@dataclass(frozen=True)
class TraceContext:
    """Correlation metadata carried across API, graph, tool, and OCI calls."""

    trace_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    case_id: str | None = None
    item_id: str | None = None
    supplier_id: str | None = None
    role: str | None = None
    agent: str | None = None
    graph_node: str | None = None
    mcp_tool: str | None = None
    approval_type: str | None = None
    approval_result: str | None = None
    status: str | None = None

    def as_metadata(self, **additional: Any) -> dict[str, Any]:
        """Return non-empty values suitable for trace and log metadata."""
        metadata = {key: value for key, value in asdict(self).items() if value is not None}
        metadata.update({key: value for key, value in additional.items() if value is not None})
        return metadata
