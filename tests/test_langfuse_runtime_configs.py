"""Private tracing configuration survives one-click service configuration preparation."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from app.config import Settings

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_env():
    return {name: "synthetic-test-only" for name in (
        "ORACLE_DB_DSN", "ORACLE_DB_USER", "ORACLE_DB_PASSWORD",
        "ORACLE_DB_WALLET_PASSWORD", "OCI_GENAI_API_KEY", "OCI_NAMESPACE",
        "OCI_BUCKET_NAME", "OCI_NOTIFICATION_TOPIC_ID")}


@pytest.mark.parametrize("flag", ["false", False, "FALSE"])
def test_disabled_export_overrides_enabled_service_environment(monkeypatch, flag):
    script = load_script("prepare_runtime_configs", monkeypatch)
    values = private_env()
    if flag is not None:
        values["LANGFUSE_TRACING_ENABLED"] = flag
    # Stale tracing keys must not be exported while disabled.
    values.update(LANGFUSE_PUBLIC_KEY="unused-public", LANGFUSE_SECRET_KEY="unused-secret")
    configs = script.build_configs(values, {"retail": "r" * 32, "supplier": "s" * 32})
    assert len(configs) == 4
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    for config in configs.values():
        assert config["LANGFUSE_TRACING_ENABLED"] is False
        assert "LANGFUSE_SECRET_KEY" not in config
        assert "LANGFUSE_PUBLIC_KEY" not in config
        # Exercise the actual private JSON settings precedence.
        monkeypatch.setenv("APP_CONFIG_JSON", json.dumps({
            "LANGFUSE_TRACING_ENABLED": config["LANGFUSE_TRACING_ENABLED"]}))
        assert Settings(_env_file=None).langfuse_tracing_enabled is False


def test_enabled_export_preserves_complete_private_overrides_in_every_service(monkeypatch):
    script = load_script("prepare_runtime_configs", monkeypatch)
    values = private_env() | {"LANGFUSE_TRACING_ENABLED": "true"}
    keys = {"retail": "r" * 32, "supplier": "s" * 32}
    with pytest.raises(ValueError, match="Enabled Langfuse requires"):
        script.build_configs(values | {"LANGFUSE_PUBLIC_KEY": "partial-override"}, keys)
    values.update(LANGFUSE_BASE_URL="https://tracing.example.invalid",
                  LANGFUSE_PUBLIC_KEY="synthetic-public", LANGFUSE_SECRET_KEY="synthetic-secret",
                  LANGFUSE_ENVIRONMENT="test", LANGFUSE_RELEASE="test-release")
    for config in script.build_configs(values, keys).values():
        assert config["LANGFUSE_TRACING_ENABLED"] is True
        assert config["LANGSMITH_TRACING"] is False
        for name in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
                     "LANGFUSE_ENVIRONMENT", "LANGFUSE_RELEASE"):
            assert config[name] == values[name]


def test_private_config_cli_preserves_literal_key_and_existing_service_settings(tmp_path, monkeypatch):
    script = load_script("configure_langfuse", monkeypatch)
    source, env, output = (tmp_path / name for name in ("service.json", "private.env", "result.json"))
    source.write_text(json.dumps({"AGENT_SERVICE_KEY": "existing-service-key"}), encoding="utf-8")
    env.write_text("LANGFUSE_BASE_URL=https://tracing.example.invalid\n"
                   "LANGFUSE_PUBLIC_KEY=synthetic-public\n"
                   "LANGFUSE_SECRET_KEY=synthetic-${UNRELATED_VAR}-secret\n", encoding="utf-8-sig")
    monkeypatch.setenv("UNRELATED_VAR", "must-not-expand")
    monkeypatch.setattr(sys, "argv", ["configure_langfuse.py", "--service", "retail-agent",
        "--config", str(source), "--langfuse-env", str(env), "--output", str(output)])
    script.main()
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["LANGFUSE_SECRET_KEY"] == "synthetic-${UNRELATED_VAR}-secret"
    assert result["AGENT_SERVICE_KEY"] == "existing-service-key"
    assert result["LANGFUSE_TRACING_ENABLED"] is True
    with pytest.raises(FileExistsError):
        script.main()


@pytest.mark.parametrize("url", ["https://host:bad", "https://host:99999", "https://host/path with-space"])
def test_private_config_rejects_malformed_endpoint_before_writing(monkeypatch, url):
    script = load_script("configure_langfuse", monkeypatch)
    with pytest.raises(ValueError):
        script.configure({}, {"LANGFUSE_BASE_URL": url,
                              "LANGFUSE_PUBLIC_KEY": "synthetic", "LANGFUSE_SECRET_KEY": "synthetic"})


def test_generated_configs_without_shared_settings_remain_disabled(monkeypatch):
    script = load_script("prepare_runtime_configs", monkeypatch)
    configs = script.build_configs(private_env(), {"retail": "r" * 32, "supplier": "s" * 32})
    monkeypatch.delenv("LANGFUSE_TRACING_ENABLED", raising=False)
    for config in configs.values():
        tracing_config = {key: value for key, value in config.items() if key.startswith("LANGFUSE_")}
        assert "LANGFUSE_SECRET_KEY" not in tracing_config
        monkeypatch.setenv("APP_CONFIG_JSON", json.dumps(tracing_config))
        assert Settings(_env_file=None).langfuse_tracing_enabled is False


def test_invalid_tracing_flag_does_not_silently_disable_tracing(monkeypatch):
    script = load_script("prepare_runtime_configs", monkeypatch)
    with pytest.raises(ValueError, match="must be true or false"):
        script.build_configs(private_env() | {"LANGFUSE_TRACING_ENABLED": "typo"},
                             {"retail": "r" * 32, "supplier": "s" * 32})
