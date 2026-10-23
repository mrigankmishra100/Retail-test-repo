# Retail Inventory Agent — AI World demo

Source branch: [`ai-world-retail-demo`](https://github.com/mrigankmishra100/Retail-test-repo/tree/ai-world-retail-demo).

Retail and Supplier workspaces for inventory analysis, supplier comparisons and
replenishment with explicit approval controls. The optional delivery integration
connects Supplier chat to a registered external question/answer API. Retail users
can select OCI models or OpenAI using a shared server key or their own session key.

## Deploy this branch

Use this source URL in Deployment Studio and select **all five services**:

```text
https://github.com/mrigankmishra100/Retail-test-repo/tree/ai-world-retail-demo
```

The existing `deployment/source-build.json` builds Linux/amd64 images for
`mcp-server`, `retail-agent`, `supplier-agent`, `backend` and `frontend`.
Start a fresh source inspection so the dashboard resolves the latest commit;
resuming an older pinned run does not pick up new source changes.

The dashboard supplies private runtime configuration, database access, service
keys and generated URLs. This release retains that build and configuration
contract. Compatibility checks use the locally reviewed
`feature/retail-distributor-side-by-side` dashboard snapshot at
`65347b23a5d55e1c8d979532ba2421fdcc925f03`; they do not certify a newer dashboard
revision or replace live deployment checks.

Read the [deployment handoff](docs/ai-world-deployment-handoff.md) before deploying,
especially for the second-stage merged Retail + Distributor application.
[DEPLOYMENT.md](DEPLOYMENT.md) contains the runtime contract, build commands and
operational limits.

## Current behavior

- Registry startup adds missing Retail/Supplier entries using generated service
  URLs and preserves existing entries and inactive states. Deactivation blocks
  subsequent routed invocations.
- Supplier registration of an active Custom delivery API also selects it for the
  current supplier session. The optional bearer-token field is always available.
  Explicit delivery questions call the connected API directly after input checks;
  other conversational questions can use the Supplier model and scoped tools.
  The registry is application-specific REST integration, not an A2A registry.
- Retail OpenAI selection uses a shared server key and the provider's model catalog.
  If no shared key is configured, the picker accepts a private session API key.
  It applies to retail chat and remaining workflow AI steps; approval controls
  remain explicit. Supplier chat retains its own configured model.
- Model comparison runs two or three models concurrently with independent context
  and read-only tools. Results appear as each finishes, retaining selected model
  order and accurate failure labels. Analytical statements are distinguished from
  unsupported business-action claims in comparison answers.
- Wide comparison tables scroll inside their cards. Cloud monitoring retains log
  summaries and Langfuse access; the detailed OCI application events browser is
  removed. Model-cost estimates appear only where reported usage and pricing
  support them; they are not an OCI bill.

See [chat evaluation](docs/chat-model-evaluation.md),
[delivery API contract](docs/delivery-agent-rest-contract.md) and
[provider and local setup](docs/cloudworld-local-changes.md).

## Credentials and storage

Credentials are supplied privately at runtime, never committed or baked into
images. For shared OpenAI, set `OPENAI_API_KEY` in the hosted **retail-agent**
private runtime JSON. Setting it only on the dashboard VM or a developer laptop
is insufficient. The [handoff](docs/ai-world-deployment-handoff.md) explains the
configuration helper and automatic versus manual provisioning. Without the key,
the OpenAI picker asks for a session API key instead; no dashboard code or runtime
configuration change is needed for this fallback. Session keys stay in server
memory and must be entered again after session expiry or a server restart.
OCI model options remain usable.

Delivery registration stores its credential separately from `agent_registry`.
Hosted connections use configured Redis, or `delivery_connections` in the existing
Oracle schema when Redis is absent. The app creates/upgrades that table on first
use; migration 006 is available for owner-managed fresh-table creation. These
comparison/UI changes introduce no additional schema changes. Local development
uses memory for delivery connections. See DEPLOYMENT.md for grants and session limits.

## Run locally

Use Python 3.12+ with the backend package installed (`python -m pip install -e ./backend`), Node.js with frontend
packages installed, and your private OCI/database configuration. From the repository
root in PowerShell, with an existing private environment file:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-chat-local.ps1 -EnvFile ".\runtime-config-private\local-openai.env" -PythonExe ".\.venv\Scripts\python.exe"
```

Adjust those two paths to your local files. In a second terminal:

```powershell
cd frontend
npm.cmd ci
$env:API_PROXY_TARGET = "http://127.0.0.1:8000"
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173. Node/npm must be installed and on PATH. Restart the
backend after Python/configuration changes. The private environment file is not
included in Git; a fresh checkout needs its own runtime setup.

## Release verification

See `deployment/source-release.json` for provenance and recorded verification.
The current release is checked with backend regressions, browser fixtures, a
production frontend build and the dashboard's five-service configuration preflight.
These offline checks do not exercise live credentials, provider quotas, OCI IAM,
Oracle schema grants or new container images. Build the five images and run the
[post-deployment checks](docs/ai-world-deployment-handoff.md#verification-after-deployment)
on the event deployment. Use one instance per service with the current local
comparison queue; multi-replica operation requires shared queue/state design.
