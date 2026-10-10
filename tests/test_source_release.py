"""Source-only packaging, tracing and unchanged-policy regressions."""
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import SecretStr
from pypdf import PdfReader

from app.config import Settings
from app.observability.langfuse import LangfuseObservabilityClient, sanitize_trace_data
from app.services.policy_terms import parse_policy_terms

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_defines_five_portable_linux_amd64_source_builds():
    manifest = json.loads((ROOT / "deployment/source-build.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["platform"] == "linux/amd64"
    assert [s["name"] for s in manifest["services"]] == [
        "mcp-server", "retail-agent", "supplier-agent", "backend", "frontend"]
    for service in manifest["services"]:
        assert service["context"] == "."
        dockerfile = (ROOT / service["dockerfile"]).read_text()
        assert "hyd.ocir.io" not in dockerfile
        assert "retail-ui-source-check" not in dockerfile
        assert "org.opencontainers.image.revision" in dockerfile
        assert "USER appuser" in dockerfile
        assert "APT::Update::Error-Mode=any" in dockerfile
        if service["target"]:
            assert f'AS {service["target"]}' in dockerfile
    assert not (ROOT / "docker-images").exists()
    ignore = (ROOT / ".dockerignore").read_text()
    for pattern in ("docker-images", "runtime-config-private/", "**/*.pem", "**/.env"):
        assert pattern in ignore


def test_seed_rows_match_all_original_policy_terms():
    seed = (ROOT / "database/seed.sql").read_text()
    rows = re.findall(r"INSERT INTO supplier_items VALUES \('([^']+)', '([^']+)', ([\d.]+), (\d+), (\d+)\);", seed)
    assert len(rows) == 12
    policies = {}
    for supplier in ("SUP001", "SUP002", "SUP003", "SUP004"):
        path = ROOT / "policies" / f"{supplier}_policy.pdf"
        content = "\n".join(page.extract_text() for page in PdfReader(path).pages)
        policies[supplier] = parse_policy_terms({
            "supplier_id": supplier, "content": content,
            "source": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }, as_of=date(2026, 10, 10))
    for supplier, item, price, lead, minimum in rows:
        product = policies[supplier]["products"][item]
        assert (Decimal(price), int(lead), int(minimum)) == (
            product["base_price"], product["lead_time_days"], product["moq"])


def test_tracing_defaults_to_disabled_and_does_not_initialize_sdk(monkeypatch):
    factory = Mock(side_effect=AssertionError("Disabled tracing must not initialize"))
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=factory))
    settings = Settings(_env_file=None)
    assert not settings.langfuse_tracing_enabled
    tracing = LangfuseObservabilityClient.from_settings(settings)
    with tracing.observation("test") as observation:
        observation.update(output={"status": "ok"})
    tracing.close()
    factory.assert_not_called()


def test_langfuse_mask_redacts_credentials_and_emails():
    result = sanitize_trace_data(data={
        "password": "example-password", "api_key": "example-key",
        "text": "synthetic@example.com Bearer example-token sk-lf-example-key",
        "value": SecretStr("example-secret"), "count": 5,
    })
    rendered = json.dumps(result)
    for private in ("example-password", "example-key", "example-token", "example-secret", "synthetic@example.com"):
        assert private not in rendered
    assert result["count"] == 5


def test_tracing_sdk_failure_does_not_break_business_work(monkeypatch):
    factory = Mock(side_effect=RuntimeError("synthetic exporter failure"))
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(Langfuse=factory))
    tracing = LangfuseObservabilityClient(enabled=True, public_key="test-public", secret_key="test-secret", base_url="https://example.invalid")
    with tracing.observation("test") as observation:
        observation.update(output="work completed")
    with pytest.raises(ValueError, match="business failure"):
        with tracing.observation("test"):
            raise ValueError("business failure")
    assert factory.call_count == 1


def test_frontend_and_backend_share_private_json_runtime_source():
    # Generated service config wins over baked Docker ENV defaults at runtime.
    from app.runtime_config import JsonSettingsMixin
    from app.frontend import FrontendSettings
    assert issubclass(Settings, JsonSettingsMixin)
    assert issubclass(FrontendSettings, JsonSettingsMixin)
