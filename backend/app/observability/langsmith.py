"""Lazy, opt-in LangSmith tracing for agent and LangGraph execution."""

from contextlib import AbstractContextManager, nullcontext
import logging
from typing import Any

from app.observability.context import TraceContext

logger = logging.getLogger(__name__)


class LangSmithObservabilityClient:
    """Create scoped LangSmith tracing contexts without import-time I/O."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        api_key: str | None = None,
        project: str = "retail-inventory-agent-poc",
        endpoint: str = "https://api.smith.langchain.com",
        workspace_id: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.api_key = api_key
        self.project = project
        self.endpoint = endpoint
        self.workspace_id = workspace_id
        self._client: Any | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "LangSmithObservabilityClient":
        """Create the integration from environment-backed settings."""
        secret = settings.langsmith_api_key
        api_key = secret.get_secret_value() if secret is not None else None
        return cls(
            enabled=settings.langsmith_tracing,
            api_key=api_key,
            project=settings.langsmith_project,
            endpoint=settings.langsmith_endpoint,
            workspace_id=settings.langsmith_workspace_id,
        )

    @property
    def is_configured(self) -> bool:
        """Return whether tracing has enough configuration to emit traces."""
        return bool(self.enabled and self.api_key and self.project and self.endpoint)

    def tracing_context(
        self,
        context: TraceContext | None = None,
        *,
        tags: list[str] | None = None,
        **metadata: Any,
    ) -> AbstractContextManager[Any]:
        """Return a LangSmith context, or a no-op context when disabled."""
        if not self.enabled:
            return nullcontext()
        if not self.is_configured:
            raise ValueError("LangSmith tracing is enabled but required configuration is missing")

        try:
            import langsmith as ls
        except ImportError as exc:  # pragma: no cover - installation concern
            raise RuntimeError("The 'langsmith' package is required when tracing is enabled") from exc

        trace_metadata = (context or TraceContext()).as_metadata(**metadata)
        return ls.tracing_context(
            enabled=True,
            client=self._get_client(ls),
            project_name=self.project,
            metadata=trace_metadata,
            tags=tags or [],
        )

    def _get_client(self, langsmith_module: Any) -> Any:
        """Create the SDK client on first traced execution."""
        if self._client is None:
            self._client = langsmith_module.Client(
                api_key=self.api_key,
                api_url=self.endpoint,
                workspace_id=self.workspace_id,
            )
            logger.info("LangSmith tracing enabled for project %s", self.project)
        return self._client
