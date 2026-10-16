"""Deployment defaults remain replaceable and never disclose project keys."""
import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.observability.langfuse import LangfuseObservabilityClient


@pytest.fixture(autouse=True)
def isolated_settings_sources(monkeypatch):
    for name in ("APP_CONFIG_JSON", "APP_CONFIG_NAMESPACE", "APP_CONFIG_BUCKET", "APP_CONFIG_OBJECT_NAME",
                 "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                 "LANGFUSE_ENVIRONMENT", "LANGFUSE_RELEASE", "LANGSMITH_TRACING"):
        monkeypatch.delenv(name, raising=False)
    # Every test process also sets this before importing the application. Keep
    # tracing off unless a test supplies synthetic project credentials.
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")


def test_environment_can_disable_default_tracing_without_sdk_initialization():
    settings = Settings(_env_file=None)
    tracing = LangfuseObservabilityClient.from_settings(settings)
    assert settings.langfuse_tracing_enabled is False
    assert tracing.is_configured is False
    assert tracing._client is None
    with tracing.observation("disabled.test"):
        pass
    assert tracing._initialization_attempted is False


def test_environment_can_replace_deployment_credentials_without_initializing_sdk(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://synthetic-tracing.example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-synthetic-override")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-synthetic-override")
    monkeypatch.setenv("LANGFUSE_ENVIRONMENT", "test")
    monkeypatch.setenv("LANGFUSE_RELEASE", "synthetic-release")
    settings = Settings(_env_file=None)
    tracing = LangfuseObservabilityClient.from_settings(settings)
    assert tracing.is_configured
    assert tracing.base_url == "https://synthetic-tracing.example.invalid"
    assert tracing.public_key == "pk-lf-synthetic-override"
    assert tracing.secret_key == "sk-lf-synthetic-override"
    assert tracing.environment == "test"
    assert tracing.release == "synthetic-release"
    assert tracing._client is None
    assert "pk-lf-synthetic-override" not in repr(settings)
    assert "sk-lf-synthetic-override" not in repr(settings)
    assert "sk-lf-synthetic-override" not in settings.model_dump_json()


def test_partial_runtime_json_can_disable_environment_enabled_tracing(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-synthetic-override")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-synthetic-override")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://synthetic-tracing.example.invalid")
    monkeypatch.setenv("APP_CONFIG_JSON", json.dumps({"LANGFUSE_TRACING_ENABLED": False,
                                                     "ENTERPRISE_AI_RESPONSES_ENABLED": False}))
    settings = Settings(_env_file=None)
    tracing = LangfuseObservabilityClient.from_settings(settings)
    assert not settings.langfuse_tracing_enabled
    assert not tracing.is_configured
    assert tracing._client is None


def test_runtime_json_replaces_credentials_and_retains_unrelated_configuration(monkeypatch):
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://environment.example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-synthetic-environment")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-synthetic-environment")
    monkeypatch.setenv("APP_CONFIG_JSON", json.dumps({
        "LANGFUSE_TRACING_ENABLED": True, "LANGFUSE_BASE_URL": "https://runtime.example.invalid",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-synthetic-json", "LANGFUSE_SECRET_KEY": "sk-lf-synthetic-json",
        "LANGFUSE_ENVIRONMENT": "test", "LANGFUSE_RELEASE": "synthetic-json-release",
        "ENTERPRISE_AI_RESPONSES_ENABLED": False, "INVENTORY_RISK_HORIZON_DAYS": 9,
    }))
    settings = Settings(_env_file=None)
    tracing = LangfuseObservabilityClient.from_settings(settings)
    assert tracing.is_configured
    assert tracing.base_url == "https://runtime.example.invalid"
    assert tracing.public_key == "pk-lf-synthetic-json"
    assert tracing.secret_key == "sk-lf-synthetic-json"
    assert tracing._client is None
    assert settings.inventory_risk_horizon_days == 9
    assert not settings.enterprise_ai_responses_enabled
    explicit_disabled = Settings(_env_file=None, langfuse_tracing_enabled=False)
    assert not LangfuseObservabilityClient.from_settings(explicit_disabled).is_configured


def test_tracing_status_reports_configuration_without_exporting_credentials(monkeypatch):
    import app.chat_api as api
    settings = Settings(_env_file=None, langfuse_tracing_enabled=True,
        langfuse_base_url="https://synthetic-tracing.example.invalid", langfuse_environment="test",
        langfuse_public_key="pk-lf-synthetic-status", langfuse_secret_key="sk-lf-synthetic-status")
    tracing = LangfuseObservabilityClient.from_settings(settings)
    monkeypatch.setattr(api, "get_container", lambda: SimpleNamespace(langfuse=tracing))
    result = api.tracing(session=object())
    assert result["status"] == "configured"
    assert result["dashboard_url"] == "https://synthetic-tracing.example.invalid"
    assert result["scope"] == "This backend service"
    rendered = json.dumps(result)
    assert "pk-lf-synthetic-status" not in rendered
    assert "sk-lf-synthetic-status" not in rendered
    assert tracing._client is None


def test_public_images_have_no_embedded_credentials(monkeypatch):
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    tracing = LangfuseObservabilityClient.from_settings(settings)
    assert settings.langfuse_tracing_enabled is False
    assert settings.langfuse_base_url is None
    assert settings.langfuse_public_key is None
    assert settings.langfuse_secret_key is None
    assert not tracing.is_configured and tracing._client is None
