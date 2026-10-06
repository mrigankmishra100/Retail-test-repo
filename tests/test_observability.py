"""Offline tests for shared trace context and lazy LangSmith setup."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.observability import LangSmithObservabilityClient, TraceContext


def test_trace_context_excludes_empty_values() -> None:
    context = TraceContext(trace_id="trace-1", case_id="case-1")

    assert context.as_metadata(graph_node="analyze", supplier_id=None) == {
        "trace_id": "trace-1",
        "case_id": "case-1",
        "graph_node": "analyze",
    }


def test_disabled_langsmith_context_is_a_no_op() -> None:
    tracing = LangSmithObservabilityClient()

    assert isinstance(tracing.tracing_context(), type(nullcontext()))


def test_enabled_langsmith_requires_credentials() -> None:
    tracing = LangSmithObservabilityClient(enabled=True)

    with pytest.raises(ValueError, match="required configuration"):
        tracing.tracing_context()


def test_configured_langsmith_client_is_created_lazily() -> None:
    sdk_context = nullcontext()
    sdk_client = Mock(return_value="client")
    tracing_context = Mock(return_value=sdk_context)
    fake_langsmith = SimpleNamespace(Client=sdk_client, tracing_context=tracing_context)
    tracing = LangSmithObservabilityClient(enabled=True, api_key="secret")

    assert tracing._client is None
    with patch.dict("sys.modules", {"langsmith": fake_langsmith}):
        result = tracing.tracing_context(TraceContext(trace_id="trace-1"), tags=["retail"])

    assert result is sdk_context
    sdk_client.assert_called_once_with(
        api_key="secret",
        api_url="https://api.smith.langchain.com",
        workspace_id=None,
    )
    tracing_context.assert_called_once_with(
        enabled=True,
        client="client",
        project_name="retail-inventory-agent-poc",
        metadata={"trace_id": "trace-1"},
        tags=["retail"],
    )
