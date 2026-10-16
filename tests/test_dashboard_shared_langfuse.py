"""Shared VM credentials reach private server configs, never public runs or frontend."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest
from app.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


hook = module("deployment/dashboard/retail_langfuse.py", "shared_langfuse_test")
installer = module("scripts/install_dashboard_langfuse.py", "installer_langfuse_test")
VALUES = {"LANGFUSE_BASE_URL": "https://tracing.example.invalid", "LANGFUSE_PUBLIC_KEY": "synthetic-public",
          "LANGFUSE_SECRET_KEY": "synthetic-secret", "LANGFUSE_TRACING_ENABLED": True}


def private_file(tmp_path, monkeypatch, values=VALUES):
    path = tmp_path / "private.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv(hook.CONFIG_ENV, str(path))
    return path


def test_vm_snapshot_is_stable_for_retries_and_only_four_servers_receive_keys(tmp_path, monkeypatch):
    path = private_file(tmp_path, monkeypatch)
    snapshot = hook.shared_tracing_snapshot()
    path.write_text(json.dumps(VALUES | {"LANGFUSE_SECRET_KEY": "changed-later"}), encoding="utf-8")
    originals = {name: {"APP_ENV": "development", "EXISTING_SETTING": "keep"} for name in [*hook.SERVICES, "frontend"]}
    before = copy.deepcopy(originals)
    configs = hook.with_shared_tracing(originals, snapshot)
    assert originals == before
    for name in hook.SERVICES:
        assert configs[name]["LANGFUSE_SECRET_KEY"] == "synthetic-secret"
        assert configs[name]["EXISTING_SETTING"] == "keep"
        assert configs[name]["LANGSMITH_TRACING"] is False
        # Exercise the real retail app loader with the injected settings.
        monkeypatch.setenv("APP_CONFIG_JSON", json.dumps({k: v for k, v in configs[name].items() if k != "EXISTING_SETTING"}))
        settings = Settings(_env_file=None)
        assert settings.langfuse_tracing_enabled
        assert settings.langfuse_secret_key.get_secret_value() == "synthetic-secret"
    assert configs["frontend"] == before["frontend"]
    assert "synthetic-secret" not in json.dumps(configs["frontend"])
    assert hook.shared_tracing_snapshot()[hook.SNAPSHOT_KEY]["LANGFUSE_SECRET_KEY"] == "changed-later"


def test_existing_runs_without_snapshot_do_not_inherit_new_vm_keys(tmp_path, monkeypatch):
    private_file(tmp_path, monkeypatch)
    configs = {"backend": {"APP_ENV": "development"}}
    assert hook.with_shared_tracing(configs, {}) == configs


def test_no_vm_file_is_optional_and_explicit_false_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv(hook.CONFIG_ENV, raising=False)
    assert hook.shared_tracing_snapshot() == {}
    private_file(tmp_path, monkeypatch, {"LANGFUSE_TRACING_ENABLED": False})
    result = hook.with_shared_tracing({"backend": {}, "frontend": {}}, hook.shared_tracing_snapshot())
    assert result == {"backend": {"LANGFUSE_TRACING_ENABLED": False}, "frontend": {}}


@pytest.mark.parametrize("changes", [
    {"LANGFUSE_SECRET_KEY": ""}, {"LANGFUSE_TRACING_ENABLED": "true"},
    {"EXTRA": "PRIVATE_SENTINEL"}, {"LANGFUSE_BASE_URL": "https://user:PRIVATE_SENTINEL@host"},
    {"LANGFUSE_BASE_URL": "http://host:bad"}, {"LANGFUSE_ENVIRONMENT": "BAD"}])
def test_invalid_vm_settings_fail_without_exposing_values(tmp_path, monkeypatch, changes):
    private_file(tmp_path, monkeypatch, VALUES | changes)
    with pytest.raises(ValueError) as error:
        hook.shared_tracing_snapshot()
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert "synthetic-secret" not in str(error.value)


def test_unreadable_or_duplicate_json_fails_without_falling_back(tmp_path, monkeypatch):
    path = private_file(tmp_path, monkeypatch)
    path.write_text('{"LANGFUSE_TRACING_ENABLED":true,"LANGFUSE_TRACING_ENABLED":false}', encoding="utf-8")
    with pytest.raises(ValueError): hook.shared_tracing_snapshot()
    path.unlink()
    with pytest.raises(ValueError): hook.shared_tracing_snapshot()


def fixture_dashboard(tmp_path, probe=False):
    studio = tmp_path / "backend/studio"
    studio.mkdir(parents=True)
    extra = ", 'runtime_probe_token': 'synthetic'" if probe else ""
    (studio / "retail_workflow.py").write_text('"""Workflow."""\ndef start():\n    private = {**generated_secrets(), **database_config' + extra + '}\n', encoding="utf-8")
    (studio / "retail_recipe.py").write_text('"""Recipe."""\ndef configurations(resources, private):\n    values = {}\n    return values\n', encoding="utf-8")
    return studio


@pytest.mark.parametrize("probe", [False, True])
def test_installer_preserves_dashboard_variants_and_is_idempotent(tmp_path, probe):
    studio = fixture_dashboard(tmp_path, probe)
    assert installer.install(tmp_path) == 2
    assert not (studio / "retail_langfuse.py").exists()
    assert installer.install(tmp_path, True) == 2
    assert installer.install(tmp_path, True) == 0
    workflow = (studio / "retail_workflow.py").read_text()
    assert "**shared_tracing_snapshot()" in workflow
    assert ("runtime_probe_token" in workflow) is probe
    assert (studio / "retail_workflow.py.before-shared-langfuse").exists()
    assert "with_shared_tracing(values, private)" in (studio / "retail_recipe.py").read_text()


def test_installer_rejects_unknown_source_without_partial_changes(tmp_path):
    studio = fixture_dashboard(tmp_path)
    path = studio / "retail_recipe.py"
    path.write_text('"""Changed recipe."""\ndef configurations():\n    return {}\n')
    before = (studio / "retail_workflow.py").read_bytes()
    with pytest.raises(ValueError): installer.install(tmp_path, True)
    assert (studio / "retail_workflow.py").read_bytes() == before
    assert not (studio / "retail_langfuse.py").exists()
