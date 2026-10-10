# Current deployment

Use deployment/active-images.json as the release-selection manifest. It lists
only the five selected tags/digests. Registry digests are immutable references;
health checks alone cannot prove the exact digest running. Deployment selection
also relies on the owner's reported rollouts.

## Images and builds

Registry prefix: hyd.ocir.io/ax4qsxvnsmtm/retail-inventory.

| Application | Tag | Private config object |
| --- | --- | --- |
| backend | resume-body-fix-20260917-v1 | backend-v1.json |
| retail-agent | mcp-json-fix-20260917-v1 | retail-agent-v1.json |
| supplier-agent | mcp-json-fix-20260917-v1 | supplier-agent-v1.json |
| mcp-server | mcp-json-fix-20260917-v1 | mcp-v1.json |
| frontend | mcp-json-fix-20260917-v1 | None; console variables only |

For future builds choose a new tag; never overwrite a released tag:

```bash
docker build --platform linux/amd64 --provenance=false --sbom=false --target backend -f backend/Dockerfile.split -t <backend-image:new-tag> .
docker build --platform linux/amd64 --provenance=false --sbom=false --target retail-agent -f backend/Dockerfile.split -t <retail-image:new-tag> .
docker build --platform linux/amd64 --provenance=false --sbom=false --target supplier-agent -f backend/Dockerfile.split -t <supplier-image:new-tag> .
docker build --platform linux/amd64 --provenance=false --sbom=false --target mcp-server -f backend/Dockerfile.split -t <mcp-image:new-tag> .
docker build --platform linux/amd64 --provenance=false --sbom=false -f frontend/Dockerfile -t <frontend-image:new-tag> .
```

The split recipe retains a pinned private OCIR Oracle Thick runtime, so registry
access is required to rebuild. backend/Dockerfile and Dockerfile.thick describe
base/runtime construction, including the native client configuration. Historical
base digests are build dependencies, not active deployment entries.

frontend/Dockerfile builds the complete React app from source. Dockerfile.refresh
is the packaging recipe used by the current release and reuses a pinned UI base.
Images listen on 8080, run as appuser and require a single linux/amd64 manifest.
Never embed local credentials, wallets or private configuration in an image.

## Runtime configuration

Use the five values in deployment/bootstrap.env.example for Backend, Retail,
Supplier and MCP, selecting the matching object name. The current private bucket
is retail-inventory-config-private, namespace ax4qsxvnsmtm. Do not also set
APP_CONFIG_JSON. Restart/redeploy to load changed JSON; there is no live reload.

Resource principals need access to their required config/wallet/policy objects
and notification topics. The GenAI API key is a separate identity for Responses
and vector access in Retail/MCP. Neither kind of credential belongs in Git.

Private JSONs are excluded. scripts/prepare_runtime_configs.py prepares role
files from private input; read --help first. It preserves supplied agent keys
but generates new MCP delegation keys: do not blindly rerun it for existing
deployments. Roll out matching role files together. Policy-vector ingestion is
explicit, not an application startup step; do not blindly repeat ingestion.

Frontend console variables:

```dotenv
APP_ENV=production
LOG_LEVEL=INFO
BACKEND_AUTH_MODE=none
FRONTEND_ALLOW_ANONYMOUS=true
BACKEND_API_CONTRACT=integrated
BACKEND_INVOKE_BASE=https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/<backend-application-ocid>/actions/invoke
```

The production label does not make anonymous access production-safe; this is a
restricted-access POC. Do not include a literal <custom_path> in endpoint bases.

## Behavior and rollout checks

Retail uses Responses/Gemini 2.5 Flash and Conversations. Supplier is deterministic.
Both agents use signed remote MCP delegation. JSON-only MCP POSTs avoid the
malformed hosted SSE response observed in the previous transport. Resume APIs
accept an omitted empty body, never approval fields. Dedicated decisions remain
mandatory. Oracle DB stores approvals, cases, outbox state and approved summaries.

Notifications use a shared POC topic; verify subscriptions before publishing.
Native File Search/vector policy retrieval is configured. NL2SQL, managed
registries and managed guardrails are disabled. Sessions/checkpoints/replay state
are process-local; retain one process/replica and expect session loss on restart.

GET /health and /ready checks startup. Backend GET /status/ready checks bounded
dependencies without inference or publishing. Frontend GET /api/ready checks
the backend proxy. These do not prove all workflows work end to end.

Correlate logs by trace_id and MCP call_id. Inspect stage, protocol_error_code and
response_validation_failed.validation_errors without recording values or keys.
Do not reapprove queued cases. Explicit resume may publish; review state before
retries and reconcile uncertain delivery.

## Repository hygiene

Runtime source, dependencies, build inputs, schema/migrations, sanitized examples,
operational utilities and current deployment docs are included. Local test
changes, diagnostic scripts, logs, audit manifests, historical release notes,
private PDFs/configs and generated bundles are excluded. Previously tracked
baseline tests remain unchanged. Tests are not prerequisites for frontend builds
on this deployment branch.
