"""Business exceptions must not bypass Langfuse payload redaction via SDK events."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.observability.langfuse import LangfuseObservabilityClient


def tracing_harness():
    raw = Mock()
    manager = Mock()
    manager.__enter__ = Mock(return_value=raw)
    manager.__exit__ = Mock(return_value=False)
    tracing = LangfuseObservabilityClient()
    tracing._client = SimpleNamespace(start_as_current_observation=Mock(return_value=manager))
    return tracing, manager, raw


@pytest.mark.parametrize("business_type", [ValueError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("cleanup_type", [None, RuntimeError, KeyboardInterrupt])
def test_original_exception_preserved_without_passing_it_to_sdk(business_type, cleanup_type, caplog):
    tracing, manager, raw = tracing_harness()
    original = business_type("PRIVATE_BUSINESS_DETAILS https://private.example.test")
    if cleanup_type is not None:
        manager.__exit__.side_effect = cleanup_type("PRIVATE_SDK_CLEANUP_DETAILS")
    with pytest.raises(business_type) as caught:
        with tracing.observation("workflow.test"):
            raise original
    assert caught.value is original
    manager.__exit__.assert_called_once_with(None, None, None)
    assert raw.update.call_args.kwargs["metadata"] == {
        "status": "failed", "error_type": business_type.__name__}
    assert raw.update.call_args.kwargs["status_message"] == business_type.__name__
    assert "PRIVATE_" not in repr(raw.update.call_args)
    assert "PRIVATE_" not in caplog.text


def test_sdk_update_failure_cannot_replace_the_business_error(caplog):
    tracing, manager, raw = tracing_harness()
    raw.update.side_effect = KeyboardInterrupt("PRIVATE_SDK_UPDATE_DETAILS")
    original = ValueError("PRIVATE_BUSINESS_DETAILS")
    with pytest.raises(ValueError) as caught:
        with tracing.observation("workflow.test"):
            raise original
    assert caught.value is original
    manager.__exit__.assert_called_once_with(None, None, None)
    assert "PRIVATE_" not in caplog.text


def test_sdk_exit_cannot_suppress_the_original_exception():
    tracing, manager, _ = tracing_harness()
    manager.__exit__.return_value = True
    original = ValueError("PRIVATE_BUSINESS_DETAILS")
    with pytest.raises(ValueError) as caught:
        with tracing.observation("workflow.test"):
            raise original
    assert caught.value is original
    manager.__exit__.assert_called_once_with(None, None, None)


def test_graph_approval_pause_is_waiting_and_payload_is_not_given_to_sdk():
    from langgraph.errors import GraphInterrupt
    tracing, manager, raw = tracing_harness()
    original = GraphInterrupt(("PRIVATE_APPROVAL_PAYLOAD",))
    with pytest.raises(GraphInterrupt) as caught:
        with tracing.observation("workflow.test"):
            raise original
    assert caught.value is original
    manager.__exit__.assert_called_once_with(None, None, None)
    update = raw.update.call_args.kwargs
    assert update["metadata"] == {"status": "waiting", "error_type": "GraphInterrupt"}
    assert update["level"] == "DEFAULT"
    assert "PRIVATE_APPROVAL_PAYLOAD" not in repr(update)


def test_actual_sdk_does_not_export_raw_exception_events(monkeypatch):
    """Exercise the real pinned SDK using an in-memory exporter and no HTTP."""
    langfuse = pytest.importorskip("langfuse")
    from importlib.metadata import version
    if version("langfuse") != "4.8.1":
        pytest.skip("The application pins Langfuse 4.8.1")
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from langgraph.errors import GraphInterrupt
    from uuid import uuid4
    import httpx

    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"service.name": "offline-exception-contract"}),
                              shutdown_on_exit=False)
    actual_constructor = langfuse.Langfuse
    monkeypatch.setattr(langfuse, "Langfuse", lambda **options: actual_constructor(
        **options, tracer_provider=provider, span_exporter=exporter))
    def no_http(*args, **kwargs):
        raise AssertionError("Offline SDK contract attempted HTTP")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_http)
    # The suite process disables deployment-default tracing. This test alone
    # enables the real SDK with synthetic keys and its in-memory exporter.
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    tracing = LangfuseObservabilityClient(enabled=True, public_key="pk-lf-" + uuid4().hex,
        secret_key="sk-lf-offline-fixture", base_url="https://offline.example.test")
    # The V5 feature still exports normal bounded, redacted prompt/tool data.
    with tracing.observation("normal.generation", as_type="generation", model="fixture-model",
            input={"messages": [{"role": "user", "content": "Synthetic inventory question"}],
                   "api_key": "PRIVATE_NORMAL_KEY"}) as generation:
        generation.update(output={"answer": "Synthetic inventory answer", "password": "PRIVATE_NORMAL_PASSWORD"},
                          usage_details={"input_tokens": 9, "output_tokens": 3, "total_tokens": 12})
    errors = [ValueError("PRIVATE_ERROR_DETAILS https://private.example.test"),
              GraphInterrupt(("PRIVATE_APPROVAL_PAYLOAD",))]
    for name, error in zip(("failed.operation", "waiting.operation"), errors):
        with pytest.raises(type(error)) as caught:
            with tracing.observation(name):
                raise error
        assert caught.value is error
    tracing.flush()
    finished = exporter.get_finished_spans()
    assert len(finished) == 3
    by_name = {span.name: span for span in finished}
    failed = by_name["failed.operation"]
    waiting = by_name["waiting.operation"]
    assert not failed.events
    assert not waiting.events
    assert failed.attributes["langfuse.observation.metadata.status"] == "failed"
    assert failed.attributes["langfuse.observation.status_message"] == "ValueError"
    assert waiting.attributes["langfuse.observation.metadata.status"] == "waiting"
    assert waiting.attributes["langfuse.observation.level"] == "DEFAULT"
    generated = by_name["normal.generation"].attributes
    assert "Synthetic inventory question" in generated["langfuse.observation.input"]
    assert "Synthetic inventory answer" in generated["langfuse.observation.output"]
    assert json.loads(generated["langfuse.observation.usage_details"]) == {"input": 9, "output": 3, "total": 12}
    serialized = json.dumps([dict(span.attributes) for span in finished])
    assert "PRIVATE_" not in serialized
    assert "https://private.example.test" not in serialized
    tracing.close()
    provider.shutdown()


def test_configured_proxy_is_used_for_https_trace_export(monkeypatch):
    langfuse = pytest.importorskip("langfuse")
    import requests
    from opentelemetry.exporter.otlp.proto.http import trace_exporter

    session = SimpleNamespace(proxies={})
    exporter = Mock()
    constructor = Mock()
    monkeypatch.setattr(requests, "Session", lambda: session)
    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", exporter)
    monkeypatch.setattr(langfuse, "Langfuse", constructor)
    tracing = LangfuseObservabilityClient(enabled=True, public_key="synthetic-public",
        secret_key="synthetic-secret", base_url="https://tracing.example.invalid",
        http_proxy="http://proxy.example.invalid:8080")
    assert tracing._get_client() is constructor.return_value
    assert session.proxies == {"http": tracing.http_proxy, "https": tracing.http_proxy}
    assert exporter.call_args.kwargs["session"] is session
    assert exporter.call_args.kwargs["endpoint"] == "https://tracing.example.invalid/api/public/otel/v1/traces"
    assert constructor.call_args.kwargs["span_exporter"] is exporter.return_value
    tracing.close()
