# Retail Inventory Agent

## Source-only deployment branch

Based on `ORA-AIMLCOE/retail-inventory-agent` branch `Deployment_v4`, commit
`2f61f6cb7035604a4e42c12f675b1585fca7e453`. This branch contains application
source, build definitions, tests, schema/seed SQL and the original supplier PDFs.
It deliberately contains **no Docker image archives, Git LFS objects, dashboard
code, private runtime configuration, wallets or credentials**.

### Build from source

Use Docker Desktop or Rancher Desktop's Docker/Moby engine with Linux containers.
`deployment/source-build.json` defines five `linux/amd64` builds for Deployment
Studio. Use this branch URL when selecting the deployment source:

`https://github.com/mrigankmishra100/Retail-test-repo/tree/Retail-inventory-deployment`

For a manual local build, from the repository root:

```powershell
$revision = git rev-parse HEAD
foreach ($service in @('mcp-server', 'retail-agent', 'supplier-agent', 'backend')) {
    docker buildx build --platform linux/amd64 --load --provenance=false --file backend/Dockerfile.source --target $service --build-arg "SOURCE_REVISION=$revision" --tag "retail-inventory/${service}:local" .
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $service" }
}
docker buildx build --platform linux/amd64 --load --provenance=false --file frontend/Dockerfile --build-arg "SOURCE_REVISION=$revision" --tag retail-inventory/frontend:local .
```

These builds use public digest-pinned bases and a checksum-pinned Oracle Instant
Client, not the original author's private OCIR base images or cached npm image.
They need access to Docker Hub, Debian, PyPI, npm and Oracle's download service.
Never pass runtime credentials as build arguments; inject private configuration
at runtime using the documented Object Storage bootstrap fields.

### V4 compatibility fixes

- Preserve the V4 chat-first UI; a local rebuild matched its four active static
  files in the original frontend image byte-for-byte.
- Recover the backend image's optional Langfuse tracing source and pin its extra
  SDK dependencies. Tracing remains disabled unless explicitly configured; the
  V4 Responses-only AI boundary and human approval/dispatch checks remain intact.
- Expose cached, bounded `/status/notifications` diagnostics in the backend,
  agents and MCP. These read OCI topic/subscription status, never send email, and
  do not return credentials or subscriber addresses.
- Correct the sample supplier prices, lead times and minimum order quantities
  to match the unchanged original policy PDFs. Only seed a new dedicated schema;
  this repository update does not modify an existing database.

The new builds are a source release, not byte-identical reproductions of the
historical images. OS package updates, rebuilt layers and the fixes change their
digests. IAM, OCI quotas, image scanning, network access, runtime configuration and
subscription confirmation still require deployment-time verification.

## Deployment_v4 application baseline

This branch snapshots the working `chat-first-workspace` on 2026-10-05.
Retail and Supplier navigation is now **Overview / Agent Chat / Request Activity**.
The chat presents workflow steps, terms review, explicit human approval and
separate dispatch controls. Greetings do not automatically resume old requests.

The local configuration uses Gemini 2.5 Flash through OCI Responses and enables
`ENTERPRISE_AI_NL2SQL_ENABLED=true`. The `query_retail_data` tool selects validated,
parameterized, read-only inventory, sales and supplier query templates; it is not
OCI native semantic-store NL2SQL or arbitrary SQL generation. Product-name lookup
is not yet reliable; use item IDs when the assistant asks for them.

**Deployment boundary:** the chat-first frontend was deployed on 2026-10-05.
Retail and MCP retained their V2 images and were rolled to NL2SQL-enabled configs.
See [the V4 deployment record](deployment/deployment-v4-release.json) for the
verified active tags, frontend digest, checks and rollback references.
That upstream snapshot originally had a V2 backend source/image mismatch. This
source branch incorporates the backend artifact's optional Langfuse integration.
The linked V4 record and V2 image manifests below are historical evidence, not a
declaration that newly built images have already been deployed or verified live.

The private `.env`, credentials, database wallets, dependencies, generated test
results are excluded. Historical image archives remain only in the upstream V4
branch. This source-only branch does not track or upload image archives. Docker
build recipes and test source are included; no working credentials are provided.

### Running the current local workspace

In the repository root, create your own virtual environment and a private
`backend/.env` from the example, then run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev,tracing]"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local-backend.ps1
```

The launcher preserves your own notification settings. Pass your own `-TopicId`
to perform a read-only check before startup. An explicitly approved dispatch in
shared POC mode can email all active topic subscribers.
Use `-EnvFile`, `-PythonExe` and `-TopicId` to override local defaults.
Process environment overrides take precedence over `.env`; restart after changes.

In a separate terminal:

```powershell
cd frontend
npm.cmd ci
$env:API_PROXY_TARGET = "http://127.0.0.1:8000"
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173. Development sessions/checkpoints are process-local.

## Historical V2 deployment reference

Source for the current single-replica OCI Hosted Applications POC: the complete
React UI, backend API, Retail agent, Supplier agent and remote MCP server.
See [DEPLOYMENT.md](DEPLOYMENT.md) and
[deployment/active-images.json](deployment/active-images.json) for current image
references. They describe the historical release; use `deployment/source-build.json`
for new builds from this branch.

## Current features

- Separate Retail and Supplier LangGraph workflows with explicit human approval.
- Retail: OCI Responses, Gemini 2.5 Flash, Conversations and read-only AI tools.
- Supplier: deterministic stock, policy and quote checks, review and approved dispatch.
- Remote MCP: signed, role-bound tools for inventory, suppliers, quotes, policies
  and approved case memory. Tool calls use JSON-only POST responses.
- OCI vector policy search and Responses File Search configuration; deterministic
  commercial calculations still use full policy documents.
- Oracle Database: cases, decisions, outbox/delivery state and completed summaries.
- OCI Object Storage: policies, database wallet and private per-service JSON config.
- OCI Notifications: approved requests/responses with duplicate-send safeguards.
- Structured logging: safe trace/call IDs, protocol codes and validation field names.

## Architecture

```text
React frontend/proxy -> Backend API -> Retail / Supplier agents
                             |                    |
                         Oracle DB           Remote MCP
                                                  |
                                  Oracle DB / Object Storage / Notifications
```

Chat cannot approve or dispatch. Persisted human decisions control commercial
actions. Current notifications use a shared POC topic, not supplier-isolated
production delivery. Never blindly retry uncertain publication.

Sessions, workflow checkpoints and replay state are process-local without Redis;
keep one process/replica. OCI Conversations does not replace this state. Restarts
may expire sessions/checkpoints. Production identity, durable shared state,
Vault integration and recipient isolation remain hardening work.

NL2SQL was excluded from the original V2 deployment configuration. Managed guardrails, agent/MCP registries,
external tracing and OCI managed long-term memory extraction are not enabled.
Application-level identity, tool and approval checks remain enforced.

The additive application **Agent registry** POC remains in the source but is not
exposed in the simplified sidebar. See [registry setup and migration instructions](database/AGENT_REGISTRY.md).
It stores hosted-agent URLs in Oracle and overrides configured remote destinations
without enabling the optional managed registries above.

## Source layout

| Path | Purpose |
| --- | --- |
| frontend/ | Full React/Vite source, styles, assets and build recipes |
| backend/app/ | API, agents, MCP, business services and OCI adapters |
| backend/Dockerfile.split | Backend/agent/MCP packaging targets |
| database/ | Schema and migrations; review before applying to an existing DB |
| scripts/prepare_runtime_configs.py | Explicit local private-configuration preparation |
| scripts/policy_vector_store.py | Explicit policy-vector inventory/ingestion utilities |
| deployment/ | Current image references and non-secret bootstrap example |

## Build the frontend

```bash
cd frontend
npm ci
npm run build
```

Use npm run dev for local UI development; the default API proxy is
http://localhost:8000. Do not bake secrets into frontend build variables.
The rebuilt HTML, favicon, JavaScript and CSS were compared byte-for-byte with
the currently served UI. The full-source build does not require an old UI bundle.

Local test additions, private configuration, policy PDFs, exported logs and
historical troubleshooting artifacts are excluded from the deployment commit.
Previously tracked baseline tests remain, but are not the full release suite.
