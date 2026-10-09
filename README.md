# Retail Inventory Agent

## Deployment_v4: local chat-first application

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
The backend source is the V2 baseline and does not include the separately deployed
Langfuse backend integration. Preserve that cloud backend rather than replacing
it with this copy. Existing V2 image manifests and the deployment instructions
below are historical references; use the V4 deployment record for current tags.

The private `.env`, credentials, database wallets, dependencies, generated test
results are excluded. The five deployed Docker image archives are available through
Git LFS in [docker-images](docker-images/README.md), including the Langfuse backend
binary (its integration source is not in this snapshot). Docker build recipes and test
source are included. Fill the example environment values locally; no working
credentials are provided in this branch.

### Running the current local workspace

In the repository root, run the backend with your existing private environment
and virtual environment (the launcher's defaults point to `C:\Projects\deployment_v3`):

```powershell
$env:RETAIL_ENV_FILE = "C:\Projects\deployment_v3\.env"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local-backend.ps1
```

The launcher enables the shared POC notification topic and checks it without
sending. An explicitly approved dispatch can email all active topic subscribers.
Use `-EnvFile`, `-PythonExe` and `-TopicId` for another machine or environment.
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
references. Container images remain in OCIR; V4 also includes the deployed image
archives through Git LFS as described above.

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
