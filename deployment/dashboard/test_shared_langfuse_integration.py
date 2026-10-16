"""Copy into Deployment Studio backend/tests to exercise the real recipe hooks offline."""
import json
import pytest
from test_retail_oneclick import worker, body, FakeCloud


def test_new_run_snapshots_shared_project_only_in_private_server_configuration(worker, tmp_path, monkeypatch):
    path = tmp_path / "shared-tracing.json"
    settings = {"LANGFUSE_BASE_URL": "https://tracing.example.invalid",
                "LANGFUSE_PUBLIC_KEY": "synthetic-shared-public", "LANGFUSE_SECRET_KEY": "synthetic-shared-secret",
                "LANGFUSE_TRACING_ENABLED": True}
    path.write_text(json.dumps(settings), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setenv("STUDIO_LANGFUSE_CONFIG_FILE", str(path))
    request = body()
    created = worker.start("alice", request)
    run = worker.db.get("retail_runs", created["id"])
    assert run["status"] == "completed", run["error"]
    private = worker.private.get("retail_" + run["id"])
    assert private["shared_langfuse"]["LANGFUSE_SECRET_KEY"] == settings["LANGFUSE_SECRET_KEY"]
    for service in ("backend", "retail-agent", "supplier-agent", "mcp-server"):
        config = json.loads(FakeCloud.instances[-1].uploads["config/" + service + ".json"])
        assert config["LANGFUSE_TRACING_ENABLED"] is True
        assert config["LANGFUSE_SECRET_KEY"] == settings["LANGFUSE_SECRET_KEY"]
    frontend = json.loads(FakeCloud.instances[-1].uploads["config/frontend.json"])
    assert not any(name.startswith("LANGFUSE_") for name in frontend)
    assert "synthetic-shared-secret" not in json.dumps(worker.view(run))
    assert "synthetic-shared-public" not in json.dumps(run)
    # Idempotent retry must not read the changed/deleted operator file.
    path.unlink()
    assert worker.start("alice", request)["id"] == run["id"]
    assert len(FakeCloud.instances) == 1


def test_invalid_shared_file_stops_before_cloud_resources_or_run_creation(worker, tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_LANGFUSE_CONFIG_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="Shared Langfuse configuration"):
        worker.start("alice", body())
    assert not worker.db.list("retail_runs")
    assert not FakeCloud.instances
