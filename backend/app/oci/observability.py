"""OCI Logging and Monitoring adapter boundary."""

from typing import Any


class OCIObservabilityClient:
    """Wrap future OCI Logging and Monitoring operations without import-time I/O."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        # TODO: Initialize OCI Logging and Monitoring clients lazily.

    def emit(self, event: dict[str, Any]) -> bool:
        """Emit a structured event in a future configured implementation."""
        # Expected context includes trace/session/conversation/case IDs, item and
        # supplier IDs, role, agent, graph node, MCP tool, approval details,
        # duration, and status. This complements LangSmith agent tracing.
        return False
