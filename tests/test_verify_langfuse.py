"""Offline verification of diagnostic evidence and credential boundaries."""
from contextlib import contextmanager
from datetime import datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_langfuse_script", Path(__file__).resolve().parents[1] / "scripts" / "verify_langfuse.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
CONFIG = {"LANGFUSE_BASE_URL": "https://langfuse.example", "LANGFUSE_PUBLIC_KEY": "private-public-key",
          "LANGFUSE_SECRET_KEY": "private-secret-key"}


class FakeTracing:
    def __init__(self, **options):
        self.options, self.name, self.closed, self.flushed = options, None, False, False

    @contextmanager
    def observation(self, **options):
        self.name = options["name"]
        assert options["input"] == {"check": "synthetic validation only"}
        assert options["metadata"]["synthetic"] is True
        yield SimpleNamespace(update=lambda **kwargs: None)

    def flush(self):
        self.flushed = True

    def close(self):
        self.closed = True


class FakeHTTP:
    def __init__(self, tracing, *, health=200, auth=200, observed=True, read_error=False):
        self.tracing, self.health, self.auth = tracing, health, auth
        self.observed, self.read_error, self.calls = observed, read_error, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, **options):
        self.calls.append((url, options))
        if url.endswith("/health"):
            return SimpleNamespace(status_code=self.health)
        assert options["auth"] == (CONFIG["LANGFUSE_PUBLIC_KEY"], CONFIG["LANGFUSE_SECRET_KEY"])
        if url.endswith("/projects"):
            return SimpleNamespace(status_code=self.auth, json=lambda: {"data": [{"id": "project-1"}]})
        if self.read_error:
            raise RuntimeError(CONFIG["LANGFUSE_SECRET_KEY"])
        assert self.tracing.flushed
        assert url.endswith("/api/public/v2/observations")
        query = options["params"]
        assert query["name"] == self.tracing.name and query["fields"] == "core,basic"
        assert datetime.fromisoformat(query["fromStartTime"]) < datetime.fromisoformat(query["toStartTime"])
        records = [{"name": self.tracing.name, "traceId": "trace-1", "id": "observation-1"}] if self.observed else [{"name": "unrelated", "traceId": "trace-1", "id": "observation-1"}]
        return SimpleNamespace(status_code=200, json=lambda: {"data": records})


def run(**http_options):
    tracing = FakeTracing()
    client = FakeHTTP(tracing, **http_options)
    result = script.verify(CONFIG, 0, client_factory=lambda **kwargs: client,
                           tracing_factory=lambda **kwargs: tracing)
    return result, tracing, client


def test_requires_received_trace_and_closes_adapter():
    report, tracing, client = run()
    assert report["status"] == "passed" and report["trace_observed"]
    assert report["trace_id"] == "trace-1"
    assert report["observation_id"] == "observation-1" and report["read_api"] == "observations_v2"
    assert report["trace_url"] == "https://langfuse.example/project/project-1/traces/trace-1"
    assert tracing.closed and len(client.calls) == 3


@pytest.mark.parametrize("options,reason", [({"health": 503}, "health_unavailable"),
                                         ({"auth": 401}, "authentication_failed"),
                                         ({"observed": False}, "trace_not_observed")])
def test_unhealthy_unauthenticated_and_unobserved_checks_fail(options, reason):
    report, tracing, _ = run(**options)
    assert report["status"] == "failed" and report["reason"] == reason
    assert not report["trace_observed"]
    if options.get("observed") is False:
        assert tracing.closed
    else:
        assert tracing.name is None


def test_exception_body_and_credentials_never_reach_report():
    report, tracing, _ = run(read_error=True)
    rendered = json.dumps(report)
    assert report["status"] == "failed" and tracing.closed
    assert CONFIG["LANGFUSE_SECRET_KEY"] not in rendered
    assert CONFIG["LANGFUSE_PUBLIC_KEY"] not in rendered
    assert "RuntimeError" not in rendered


@pytest.mark.parametrize("url", ["https://user:password@host", "https://host/?secret=value",
                                 "https://host/#fragment", "file:///tmp/langfuse", "http://host\nvalue"])
def test_bad_endpoint_never_contacts_network(url):
    def unexpected(**kwargs):
        pytest.fail("Invalid configuration contacted the network")
    report = script.verify(CONFIG | {"LANGFUSE_BASE_URL": url}, client_factory=unexpected)
    assert report["status"] == "failed" and report["reason"] == "configuration_invalid"


def test_poll_waits_for_matching_evidence_within_budget():
    tracing = FakeTracing()
    client = FakeHTTP(tracing, observed=False)
    ticks = [0]
    def sleep(seconds):
        ticks[0] += seconds
        client.observed = True
    report = script.verify(CONFIG, 5, client_factory=lambda **kwargs: client,
                           tracing_factory=lambda **kwargs: tracing, clock=lambda: ticks[0], sleep=sleep)
    assert report["status"] == "passed" and ticks[0] == 2
    assert len(client.calls) == 4 and tracing.closed


def test_observation_id_alone_is_not_accepted_as_trace_id():
    tracing = FakeTracing()
    client = FakeHTTP(tracing)
    original_get = client.get
    def get(url, **options):
        response = original_get(url, **options)
        if url.endswith("/api/public/v2/observations"):
            response.json = lambda: {"data": [{"name": tracing.name, "id": "observation-1"}]}
        return response
    client.get = get
    report = script.verify(CONFIG, 0, client_factory=lambda **kwargs: client,
                           tracing_factory=lambda **kwargs: tracing)
    assert report["status"] == "failed" and not report["trace_observed"]
    assert "trace_id" not in report and tracing.closed
