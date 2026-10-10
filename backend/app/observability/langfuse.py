"""Lazy, opt-in and fail-open Langfuse tracing with bounded redaction."""

from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import base64
import logging
import re
from typing import Any, Iterator

from pydantic import BaseModel, SecretStr

from app.observability.context import TraceContext

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_LANGFUSE_KEY = re.compile(r"\b(?:sk|pk)-lf-[A-Za-z0-9_-]+\b")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|secret|token|api[_-]?key|service[_-]?key|"
    r"private[_-]?key|idempotency[_-]?key)$",
    re.IGNORECASE,
)
_INFRASTRUCTURE_KEYS = {
    "config_dir", "config_file", "dsn", "endpoint", "object_name", "opc_request_id",
    "policy_source", "provider_id", "wallet", "wallet_password",
}
_MAX_DEPTH = 6
_MAX_ITEMS = 50
_MAX_STRING = 4000
_UNSET = object()


def sanitize_trace_data(value: Any = _UNSET, *, data: Any = _UNSET,
                        _depth: int = 0, **_kwargs: Any) -> Any:
    """Return JSON-safe, bounded trace data with credentials and PII removed.

    Langfuse invokes custom mask callbacks with a ``data`` keyword. Internal
    callers use the positional ``value`` argument, so accepting both keeps one
    redaction path for application payloads and SDK export masking.
    """
    if data is not _UNSET:
        value = data
    if value is _UNSET:
        value = None
    if _depth >= _MAX_DEPTH:
        return "[depth-limited]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        sanitized = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                sanitized["_truncated"] = True
                break
            name = str(key)
            lowered = name.lower()
            if _SENSITIVE_KEY.search(lowered) or lowered in _INFRASTRUCTURE_KEYS:
                sanitized[name] = REDACTED
            else:
                sanitized[name] = sanitize_trace_data(item, _depth=_depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        result = [sanitize_trace_data(item, _depth=_depth + 1) for item in values[:_MAX_ITEMS]]
        if len(values) > _MAX_ITEMS:
            result.append("[items-truncated]")
        return result
    text = str(value)
    text = _EMAIL.sub("[redacted-email]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _LANGFUSE_KEY.sub(REDACTED, text)
    return text if len(text) <= _MAX_STRING else text[:_MAX_STRING] + "...[truncated]"


class SafeObservation:
    """Small wrapper that prevents telemetry failures from affecting application work."""

    def __init__(self, observation: Any | None = None) -> None:
        self._observation = observation

    def update(self, *, output: Any = None, metadata: dict[str, Any] | None = None,
               level: str | None = None, status_message: str | None = None,
               usage_details: dict[str, int] | None = None) -> None:
        if self._observation is None:
            return
        values = {
            "output": sanitize_trace_data(output),
            "metadata": sanitize_trace_data(metadata or {}),
        }
        if level is not None:
            values["level"] = level
        if status_message is not None:
            values["status_message"] = sanitize_trace_data(status_message)
        if usage_details is not None:
            values["usage_details"] = usage_details
        try:
            self._observation.update(**values)
        except Exception:
            logger.warning("Langfuse observation update failed; application execution continues")


class LangfuseObservabilityClient:
    """Create Langfuse observations lazily and never fail application operations."""

    def __init__(self, *, enabled: bool = False, public_key: str | None = None,
                 secret_key: str | None = None, base_url: str | None = None,
                 http_proxy: str | None = None,
                 environment: str = "poc", release: str | None = None) -> None:
        self.enabled = enabled
        self.public_key = public_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.http_proxy = http_proxy
        self.environment = environment
        self.release = release
        self._client: Any | None = None
        self._initialization_attempted = False
        self._closed = False

    @classmethod
    def from_settings(cls, settings: Any) -> "LangfuseObservabilityClient":
        public = getattr(settings, "langfuse_public_key", None)
        secret = getattr(settings, "langfuse_secret_key", None)
        return cls(
            enabled=getattr(settings, "langfuse_tracing_enabled", False),
            public_key=public.get_secret_value() if public is not None else None,
            secret_key=secret.get_secret_value() if secret is not None else None,
            base_url=getattr(settings, "langfuse_base_url", None),
            http_proxy=getattr(settings, "langfuse_http_proxy", None),
            environment=getattr(settings, "langfuse_environment", "poc"),
            release=getattr(settings, "langfuse_release", None),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.public_key and self.secret_key and self.base_url)

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if not self.is_configured or self._initialization_attempted:
            return None
        self._initialization_attempted = True
        try:
            from langfuse import Langfuse
            options: dict[str, Any] = dict(
                public_key=self.public_key,
                secret_key=self.secret_key,
                base_url=self.base_url,
                tracing_enabled=True,
                environment=self.environment,
                release=self.release,
                mask=sanitize_trace_data,
            )
            if self.http_proxy:
                import requests
                from langfuse._version import __version__ as langfuse_version
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                session = requests.Session()
                session.proxies = {"http": self.http_proxy}
                auth = base64.b64encode(
                    f"{self.public_key}:{self.secret_key}".encode("utf-8")
                ).decode("ascii")
                options["span_exporter"] = OTLPSpanExporter(
                    endpoint=f"{str(self.base_url).rstrip('/')}/api/public/otel/v1/traces",
                    headers={
                        "Authorization": f"Basic {auth}",
                        "x-langfuse-sdk-name": "python",
                        "x-langfuse-sdk-version": langfuse_version,
                        "x-langfuse-public-key": self.public_key,
                        "x-langfuse-ingestion-version": "4",
                    },
                    session=session,
                )
                logger.info("Langfuse trace export uses the configured HTTP proxy")
            self._client = Langfuse(**options)
            import atexit
            atexit.register(self.close)
            if str(self.base_url).lower().startswith("http://"):
                logger.warning("Langfuse uses HTTP; restrict this POC to synthetic non-sensitive data")
            logger.info("Langfuse tracing enabled environment=%s", self.environment)
        except Exception:
            logger.warning("Langfuse initialization failed; application execution continues")
        return self._client

    @contextmanager
    def observation(self, name: str, *, as_type: str = "span", input: Any = None,
                    metadata: dict[str, Any] | None = None,
                    context: TraceContext | None = None,
                    model: str | None = None) -> Iterator[SafeObservation]:
        client = self._get_client()
        if client is None:
            yield SafeObservation()
            return
        attributes = (context or TraceContext()).as_metadata(**(metadata or {}))
        try:
            options = dict(
                name=name,
                as_type=as_type,
                input=sanitize_trace_data(input),
                metadata=sanitize_trace_data(attributes),
            )
            if model is not None:
                options["model"] = model
            manager = client.start_as_current_observation(**options)
            raw = manager.__enter__()
        except Exception:
            logger.warning("Langfuse observation start failed; application execution continues")
            yield SafeObservation()
            return

        observation = SafeObservation(raw)
        try:
            yield observation
        except BaseException as exc:
            observation.update(level="ERROR", status_message=type(exc).__name__,
                               metadata={"status": "failed", "error_type": type(exc).__name__})
            try:
                manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                logger.warning("Langfuse observation close failed; application error preserved")
            raise
        else:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                logger.warning("Langfuse observation close failed; application execution continues")

    def flush(self) -> None:
        """Flush buffered events without making shutdown depend on telemetry."""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception:
            logger.warning("Langfuse flush failed; application shutdown continues")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.flush()
        if self._client is None:
            return
        shutdown = getattr(self._client, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                logger.warning("Langfuse shutdown failed; application shutdown continues")
