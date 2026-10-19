# V5 one-click deployment test

## Hosted registry proxy correction (2026-10-19)

The initial Cloudworld commit `0fe74b7` omitted delivery connection and registry
DELETE routes from the Python frontend's integrated API allowlist. Registration
worked, but connection status/connect/disconnect and deletion returned 404 before
reaching the backend. Local Vite bypassed that proxy. This release adds those exact
routes and tests the real frontend proxy against the authenticated backend API.

Rebuild and replace the **frontend service image** from this updated branch; it
contains both React assets and `app.proxy_routes`. Restarting the old image or only
rebuilding the backend does not fix the issue. If the other four services already
use `0fe74b7`, they need no change for this correction. Otherwise rebuild all five
from the same new commit. Start fresh source inspection so the dashboard does not
reuse a run pinned to the older commit. Existing registered entries can be reused:
refresh, reconnect from that entry, and verify **Connected for this session**.
No dashboard configuration or database migration is needed for this route fix.

The compatibility checker now checks registry API routes against the hosted proxy
allowlist as well as generated service settings. Live OCI verification of the
replacement image is still required after deployment.

## Cloudworld update: supplier and delivery chat (2026-10-19)

The release branch is `R-I-Cloudworld`, created from the Demo checkout. Rebuild all five source services
together after releasing these changes. The release notes below describe historical
checks; they do not verify this new code in a live tenancy.

Supplier and MCP source images enable dedicated chat prompt guardrails. The supplier
worker delegates chat to the configured MCP model when the dashboard leaves supplier
inference disabled. The Delivery REST tool runs under the signed supplier identity
and reads its private session connection from the same store as the gateway:
configured Redis, or the existing Oracle app database when resource-principal
deployment has no Redis endpoint. Local development keeps the memory cache.
No new dashboard flags or embedded credentials are required.

The distributor adapter uses POST `/aws-app/api/v1/answers` with a `question`
body and an optional bearer integration token saved during registration. Public HTTPS IPs and
custom ports are supported with certificate verification. The supplied endpoint
on port 8081 passed a live adapter test locally. Preserve outbound access to that
port and register/connect the token after deployment; source changes alone do not
register the external service or share supplier sessions. Its current knowledge is
fictional demo data; that metadata stays in tool results and tracing. Supplier chat
displays the API answer directly, without a model rewrite or app-added disclaimer.
Rebuild gateway, agents, MCP and
frontend together; old image archives do not include this contract update.

The dashboard may use its own bundled schema. Custom registration works with its
existing registry table and needs no Delivery DDL or migration 005. Legacy DELIVERY
entries remain supported. In a supplier session, register an active Custom entry,
select **Use for delivery questions in this session**, and enter **Bearer token
(optional)** before submitting. Blank creates an unauthenticated connection; the
external API must allow that. The current distributor endpoint requires a token.
Existing entries retain Connect delivery API for reconnecting. The selected entry and token
are stored together in the private session connection store and take effect immediately without
changing runtime configuration or rebuilding images. One selected delivery connection
per supplier session; connecting another replaces the prior connection. Reconnect
after session expiry or upgrading old connections. Existing Langfuse settings enable
registry, connection and REST observations; credentials are omitted and unavailable
telemetry does not block application work.
Supplier delivery capability is now checked per turn; unconnected/inactive entries
are not advertised as available tools or carrier catalogs. This is application
code, shared by local execution and hosted supplier-to-MCP delegation. Rebuild the
updated source services; existing images do not gain this behavior from a flag.
See [the delivery contract](docs/delivery-agent-rest-contract.md) for endpoint,
token, session setup, network requirements and AWS-owner responsibilities.

The registry now reads delivery connection status from the authenticated backend
and displays a persistent session badge plus inline connection errors. The token
input intentionally clears after submission. Deploy the matching frontend and
gateway together for `GET /registry/agents/delivery-connection`; an older gateway
cannot serve this status route. Locally, restart the backend and refresh the UI.
This confirms cached configuration; remote API authorization is checked on use.

The reviewed dashboard snapshot does not configure Redis. The app therefore
creates the additive `delivery_connections` table in the existing Oracle app
schema on first use. Its creation grant is provided/checked by that snapshot's
schema setup. No dashboard edits are required. If the schema owner has removed
CREATE TABLE permission, they can apply `database/migrations/006_delivery_connections.sql`
once for a fresh table. The app also upgrades earlier credential-only connection tables
with an explicit `auth_mode` and nullable token; existing connections remain bearer-authenticated.
No dashboard schema edit is required. Tokens never enter images, registry list responses or model prompts. Reads
enforce expiry and connection operations remove expired rows; active tokens are
deleted on Disconnect. This fallback is specific to delivery connections; general
session and workflow state still require shared Redis for multiple replicas.

Registry entries can now be deleted after deactivation and confirmation. The
authenticated DELETE route removes only that Oracle registry row, not the hosted
application or chat history. No schema migration or dashboard change is needed;
rebuild the frontend and gateway with the updated source. Deletion is recorded as
`registry.delete` when Langfuse is configured. Existing delivery session bindings
cannot invoke a deleted entry and expire with their sessions.

The backend source image enables `AGENT_REGISTRY_BOOTSTRAP_ENABLED=true`. At
startup it registers missing Retail/Supplier roles using `AGENT_ENDPOINTS` from
deployment configuration. Existing registrations, including inactive ones, are
preserved. Deleting the last entry of a configured role allows bootstrap to recreate
it on restart; use deactivation to keep a role disabled. Local execution without
bootstrap and hosted endpoints does not automatically populate these two rows.

### Compatibility review on 2026-10-19

The requested `Srichand-Oracle/Deployment_Automation` repository is now private.
The newest `Deployment_optimized` commit could not be retrieved with this session's
access. Validation uses the existing local dashboard snapshot; it must not be
treated as verification of the latest private branch. Its manifest, five source
build targets, generated settings, hosted supplier/MCP routing, bootstrap defaults
and shared delivery-store selection are checked by `scripts/check_dashboard_compatibility.py`.
Repeat that command with an updated authorized checkout before release. Start a
fresh source inspection and rebuild all five services from the newly confirmed
app commit; old archives and previously pinned source runs will not contain these
changes. Cloud deployment and live Oracle DDL remain to be tested on the VM.

## Historical source-release checks

Updated from V5 source commit `f81835afaaebc02b0edbf8c1bd4555df71ad9397`.
Includes OpenAI/Anthropic/Gemini model catalogs, persistent in-chat workflow-step
snapshots and MCP control events. Existing optional Langfuse tracing, portable
source images and runtime registration are preserved. Reviewed dashboard commit:
`5162b3e` on `Deployment_optimized` (manifest and settings preflight rechecked).
The current dashboard recipe explicitly disables OCI ApplyGuardrails and enables
NL2SQL for Retail/MCP; adjust the deployment recipe if different flags are needed.
All five Linux/amd64 source images passed the latest dashboard's settings
preflight, permission/region overlays, and startup checks through its optional
runtime-access wrapper. These tests used synthetic configuration, networking
disabled, read-only containers and UID 65532. Live OCI deployment and IAM access
still require validation by the dashboard during your deployment.

Source: https://github.com/mrigankmishra100/Retail-test-repo/tree/V5-one-click-deployment

Select **all five services** in Deployment Studio and use its source-build flow.
The manifest is `deployment/source-build.json`; backend services build from
`backend/Dockerfile.source`, and the frontend from `frontend/Dockerfile`.
Historical image manifests in this repository are not the release selection for
this source branch. Do not select old image archives or the private-base Dockerfiles.

## Compatibility with the reviewed dashboard

- The exact five-service manifest, build targets and runtime module entrypoints
  remain compatible with `Deployment_optimized` at the commit above.
- Responses accepts both canonical project OCID regions and SDK aliases such as
  `hyd`. The actual project OCID is preserved; wrong-region projects are rejected.
  The validator matches the dashboard's reviewed replacement fingerprint.
- Images normalize public Python code and frontend assets to readable modes even
  when the VM checkout uses umask 0077. Runtime secrets and wallet files are outside
  this normalization. Images retain their non-root `appuser` default.
- The dashboard can add its authenticated runtime-access wrapper. Startup, health,
  readiness and unauthenticated rejection were tested through that wrapper.
  The test did not execute its cloud access or model-inference probes.
- Database schema and policy documents match the dashboard's bundled assets.
  The seed data matches; only a SQL comment differs.

Update the installed Deployment Studio separately to that reviewed revision (or
recheck a newer revision). Updating this app branch does not update the dashboard
running on your VM. The dashboard's source review pins a commit: start a new source
inspection/deployment to pick up this branch's latest code rather than resuming an
older pinned build.

To recheck configuration compatibility against a local dashboard checkout:

```powershell
python scripts/check_dashboard_compatibility.py --dashboard-root "C:\path\to\Deployment_Automation" --region ap-hyderabad-1
```

Use the app's installed Python environment. This runs the dashboard's real
manifest validator and settings preflight with synthetic canonical/short-region
configurations for all five services; it makes no database, model or OCI calls.

## Deployment and registration responsibilities

The dashboard creates resources and supplies private JSON configuration through
the existing Object Storage bootstrap. It supplies the generated Retail and
Supplier invoke URLs as `AGENT_ENDPOINTS` and their matching service keys as
`AGENT_SERVICE_KEYS` to the gateway. Agents receive their individual key, remote
MCP endpoint, MCP signing key and shared database configuration.

This branch handles runtime registration and enforcement in application code:

| Source image | Registry enforcement | Runtime registration |
| --- | --- | --- |
| backend | Enabled | Insert missing Retail/Supplier roles from `AGENT_ENDPOINTS` |
| retail-agent | Enabled | Read shared registry before relevant execution |
| supplier-agent | Enabled | Read shared registry before relevant execution |
| mcp-server | Not the agent gate | Existing authenticated MCP controls |
| frontend | API client | Registry page calls the gateway |

Image defaults are `AGENT_REGISTRY_ENFORCEMENT_ENABLED=true` in the gateway and
both agents, and `AGENT_REGISTRY_BOOTSTRAP_ENABLED=true` in the gateway only.
The current reviewed dashboard recipe omits these fields, so those defaults apply.
Private JSON configuration overrides image defaults; a future dashboard must not
set enforcement to false if deactivation is intended to block execution.
`ENTERPRISE_AI_AGENT_REGISTRY_ENABLED` and `ENTERPRISE_AI_MCP_REGISTRY_ENABLED`
are separate optional adapters and are not these application registry controls.

All three services must use the same database schema. The existing dashboard's
bundled schema already contains `agent_registry` and `uq_registry_active_type`.
On a fresh deployment, startup adds two active entries with the generated URLs.
It does not call the agents or store service keys in the registry. Validation or
database failures stop gateway startup rather than silently skipping registration.

Bootstrap never updates, replaces or reactivates an existing role. On a reused
database with old URLs, deactivate the old entry, register the new URL using the
correct **Retail** or **Supplier** role, then activate the replacement. Only one
entry per routed role may be active. A Custom entry is stored in the directory
and does not control the Retail or Supplier workflow.

## Registry demo

1. Finish the dashboard deployment and open the frontend. Sign in as Retail manager.
2. Open Agent registry. Confirm both generated URLs and active RETAIL/SUPPLIER entries.
3. In Agent Chat, ask: **Can you show items at risk?** Confirm the normal response.
4. Deactivate **Retail Agent**. Submit a new query. It must be blocked as inactive;
   no new request should be forwarded to the Retail application. A model catalog
   refresh can also show inactive while Retail is disabled.
5. Activate Retail again and retry. The registry page remains available while an
   agent is disabled. A gateway restart must not reactivate a disabled entry.
6. For Supplier, open a supplier session and use an existing workflow/request.
   With Supplier inactive, a new supplier workflow action must be blocked.
   Activate it and retry. Do not send or approve real orders just to test this gate.

Deactivation prevents new gated invocations; it does not undeploy the OCI service,
cancel a request already running, remove historical messages, or disable the other
role. Retail policy/inventory tools may still work when only Supplier is disabled:
those tools do not necessarily invoke the Supplier agent. The demo proves an
application registry gate, not an OCI platform-wide service shutdown.

## Feature configuration and current limits

- V5 source is present, but the dashboard recipe still selects its baseline model
  and explicitly disables ApplyGuardrails. Enable guardrails in private runtime
  configuration only with the required permissions and supported endpoint.
- OCI logs require configured log/log-group IDs and IAM permissions. The viewer
  does not provision a log destination. Estimated spend is not an OCI invoice.
- External model keys are supplied at runtime through the UI, never at image build.
  Regional evaluation is not offered through the remote Retail-agent route.
- Chat metrics use writable `/tmp/chat-metrics.sqlite3` in these non-root images.
  They are process-local and ephemeral. Sessions, temporary model connections and
  live activity also need appropriate shared storage/routing for multi-replica
  operation. This branch targets the current single-instance-per-service POC.
- Runtime URLs and keys must match the newly deployed services. Registry entries
  alone cannot make an unrelated application implement the signed agent contract.

## Optional local source build and smoke check

Use Docker with a running Linux engine. No cloud credentials are needed to build.
From the checkout root in PowerShell:

```powershell
$revision = git rev-parse HEAD
foreach ($service in @('mcp-server', 'retail-agent', 'supplier-agent', 'backend')) {
    docker buildx build --platform linux/amd64 --load --provenance=false --file backend/Dockerfile.source --target $service --build-arg "SOURCE_REVISION=$revision" --tag "retail-v5-source-validation:$service" .
    if ($LASTEXITCODE -ne 0) { throw "Build failed: $service" }
}
docker buildx build --platform linux/amd64 --load --provenance=false --file frontend/Dockerfile --build-arg "SOURCE_REVISION=$revision" --tag retail-v5-source-validation:frontend .
if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
python scripts/smoke_source_images.py --prefix retail-v5-source-validation
```

The smoke checker runs read-only containers as UID 65532 with networking disabled, checks
startup/health/readiness, the unsigned-agent boundary, no-send notification
diagnostics, dependencies and Oracle Thick client loading. It does not prove
live OCI permissions or connectivity. Builds need Docker Hub, Debian, PyPI, npm
and Oracle download access. Supply credentials only through private runtime
configuration; never through source files or build arguments.

## Shared Langfuse integration

Public images load Langfuse keys exclusively from runtime configuration. The SDK
is installed; tracing defaults to disabled unless the private settings enable it.
The already-hosted Langfuse instance is shared across deployments.

For automatic one-click injection, the dashboard VM operator installs the included
hook once and sets `STUDIO_LANGFUSE_CONFIG_FILE` to an operator-only JSON file.
New runs snapshot it into encrypted private state and add it to the four server
configurations, never the frontend. Existing runs and retries keep their original
settings. No Langfuse resources are provisioned by the retail deployment.

See [Dashboard shared Langfuse setup](docs/dashboard-shared-langfuse.md) for the
installer, service configuration and verification steps. Publishing this branch
alone does not install the hook on the dashboard VM. The app's Cloud monitoring
panel reports configuration only; verify actual traces after rollout.
