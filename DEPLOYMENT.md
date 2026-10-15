# V5 one-click deployment test

Updated from V5 source commit `f81835afaaebc02b0edbf8c1bd4555df71ad9397`.
Includes OpenAI/Anthropic/Gemini model catalogs, persistent in-chat workflow-step
snapshots and MCP control events. Existing optional Langfuse tracing, portable
source images and runtime registration are preserved. Reviewed dashboard commit:
`4900f90ca3cae3f40634c39169e52694762f81ed` on `Deployment_optimized`.
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
