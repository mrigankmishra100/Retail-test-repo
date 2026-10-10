"""No-cloud startup checks for locally built source images. No volumes or published ports."""
import argparse
import json
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--docker", default="docker")
parser.add_argument("--prefix", default="retail-v4-source-validation")
args = parser.parse_args()

check = '''
import importlib, json, os, subprocess
from starlette.testclient import TestClient
role = os.environ["SMOKE_ROLE"]
if role == "frontend":
    module = importlib.import_module("app.frontend")
elif role in {"retail-agent", "supplier-agent"}:
    module = importlib.import_module("app.agent_main")
elif role == "mcp-server":
    module = importlib.import_module("app.mcp.server")
else:
    module = importlib.import_module("app.main")
with TestClient(module.app) as client:
    for path in ("/health", "/ready"):
        response = client.get(path)
        assert response.status_code == 200, (role, path, response.status_code)
    if role == "frontend":
        response = client.get("/")
        assert response.status_code == 200 and "<html" in response.text
    else:
        response = client.get("/status/notifications")
        assert response.status_code == 503
        assert response.json()["reason"] == "topic_not_configured"
        assert response.json()["publish"] == "not_tested"
    if role in {"retail-agent", "supplier-agent"}:
        assert client.post("/internal/agent/invoke", json={}).status_code == 401
if role != "frontend":
    import oracledb
    oracledb.init_oracle_client()
    assert not oracledb.is_thin_mode()
import langfuse
subprocess.run(["python", "-m", "pip", "check"], check=True)
print(json.dumps({"service": role, "startup_and_probes": "passed", "cloud_access": "not_used"}))
'''

for service in ("backend", "mcp-server", "retail-agent", "supplier-agent", "frontend"):
    command = [args.docker, "run", "--rm", "--network", "none", "--read-only",
               "--tmpfs", "/tmp:rw,nosuid,size=128m", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--memory", "1g", "--cpus", "1", "-i"]
    variables = {
        "APP_ENV": "development", "OCI_AUTH_MODE": "api_key", "SMOKE_ROLE": service,
        "AGENT_SERVICE_KEY": "x" * 43,  # Synthetic test input; never used for a live call.
        "BACKEND_INVOKE_BASE": "https://example.invalid", "BACKEND_AUTH_MODE": "none",
        "FRONTEND_ALLOW_ANONYMOUS": "true", "BACKEND_API_CONTRACT": "integrated",
    }
    for key, value in variables.items():
        command.extend(["--env", key + "=" + value])
    command.extend(["--entrypoint", "python", args.prefix + ":" + service, "-"])
    result = subprocess.run(command, input=check, text=True, capture_output=True, timeout=120)
    if result.returncode:
        print(result.stdout)
        print(result.stderr)
        raise SystemExit(f"Container smoke check failed: {service}")
    print(json.dumps({"service": service, "startup_and_probes": "passed", "network": "none"}), flush=True)
