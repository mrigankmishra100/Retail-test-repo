# Retail Inventory Agent — V5 deployment test

V5 application source from `ORA-AIMLCOE/retail-inventory-agent` commit
`d8d38dc122cbab84b3bf29b3a0ee37513a688335`, adapted for the existing Deployment
Studio source-build contract. The public branch retains its public history;
private repository history, credentials and runtime configuration are not imported.

Use this source URL in the one-click dashboard and select all five services:

https://github.com/mrigankmishra100/Retail-test-repo/tree/R-I-Cloudworld

The dashboard resolves the branch to an immutable commit and builds the source
using `deployment/source-build.json`. It creates the resources, private runtime
configuration, service keys and hosted-application URLs. No dashboard code changes
are needed for this branch's registry demo with the locally reviewed recipe.

## What changed

- V5 chat, model selection, external-model comparison, evaluation UI, security
  details, query evidence and OCI log viewer.
- Portable Linux/amd64 builds for `mcp-server`, `retail-agent`, `supplier-agent`,
  `backend` and `frontend`, with public pinned base images and Oracle Thick client.
- Gateway startup inserts missing Retail and Supplier registry entries using the
  deployment's generated URLs. Both have the correct routed role and start active.
- Registry enforcement is enabled in the gateway and both agent images.
  Deactivation blocks subsequent invocations; activation restores routing.
- Existing entries, URLs and inactive states are preserved across restarts.
- Existing optional Langfuse tracing and no-send notification diagnostics remain.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the runtime contract, demo steps, local build
commands and limits. `deployment/source-release.json` records provenance and checks.

## Updated chat and evaluation build (2026-10-16)

Rebuild all five source services from the newly pushed commit. The app contains the
12-model-call / 24-tool-call analysis budget, final-answer reservation, model-assisted
project relevance check, OCI GPT-OSS system-message compatibility, and compact
comparison UI. These are source changes, not settings the dashboard must enable.
OCI ApplyGuardrails stays enabled on chat prompts through the source retail image's
`CHAT_PROMPT_GUARDRAILS_ENABLED=true` default. Private runtime overrides still apply.
Old images and already resolved deployment runs do not acquire new branch changes.
See [deployment parity and analysis behavior](docs/model-operations.md).

## Supplier chat and registry update (2026-10-19, R-I-Cloudworld)

Supplier typed prompts now reach the backend instead of browser-only canned replies.
The supplier conversation applies OCI input guardrails, restricts tools to the
authenticated supplier, and filters pending approvals before pagination. Source
supplier and MCP images enable `CHAT_PROMPT_GUARDRAILS_ENABLED=true` independently
of the dashboard's older disabled flag. Rebuild all five services together.

The reviewed dashboard leaves supplier inference disabled. In that topology the
supplier worker delegates a typed `answer_supplier_question` call to the already
AI-configured MCP service, where guardrails and the supplier-only conversation run.
No retail identity or retail tool permissions are borrowed. Locally, configured
supplier inference executes directly. Native shared File Search is not used for
supplier chat; policy access remains supplier scoped. Session chat context is kept
in the configured cache (last 20 messages, at most 120,000 characters, session TTL;
process memory when Redis is absent),
while the browser keeps the visible transcript. OCI guardrail IAM access is needed
on the service executing the checks. Explicit private configuration can override
image defaults. Validate resource-principal access after deployment.

AWS delivery REST registration and a supplier-only `ask_delivery_agent` tool are now
included in this source release. See the
[delivery REST contract and setup](docs/delivery-agent-rest-contract.md) for the AWS
owner and deployment steps. Choose CUSTOM and any name (for example Delivery agent),
select **Use for delivery questions in this session**, and optionally enter its bearer
token before clicking **Register agent**. This saves the connection in the same UI
action. Blank means no Authorization header; the current distributor endpoint still
requires its token. Existing entries can be connected or reconnected from their row.
That connection selects
the destination; names and registration alone do not route chat. Custom registration
needs no Delivery schema migration. Legacy DELIVERY entries remain supported.
Registry changes, connections and REST calls have dedicated Langfuse observations
when existing runtime tracing is configured. Hosted delivery connections use the
existing Oracle app database when Redis is absent, so gateway and MCP share them.
The connection table is created on first use; migration 006 is the owner-run
alternative where CREATE TABLE is not permitted. Local development retains memory.
The distributor adapter now matches the owner's `/aws-app/api/v1/answers`
question/answer API, supports its HTTPS public IP on port 8081, and retains source
references. Supplier chat displays the API answer directly without a model rewrite
or app-added demo disclaimer; source/demo metadata remains in tool results and tracing.
Live bearer authentication and response
validation passed locally; hosted OCI end-to-end verification remains pending.

## Initial source-release verification (historical)

- 237 Python tests and 9 subtests passed, including UI API deactivation followed
  by the actual signed-client routing boundary, for both Retail and Supplier.
- 57 mocked browser tests passed for chat, evaluation and registry.
- Production frontend build passed; locked npm install reported no vulnerabilities.
- All five generated configurations from the locally available dashboard recipe
  were accepted; its source-build validator accepted the manifest and its bundled
  schema contains the registry table and uniqueness index.

Docker was not running on the preparation machine, so the new Linux images and
container smoke checks have not been executed for this release. OCI deployment,
live model calls and the deployed deactivation demo remain to be tested through
the dashboard. No cloud resources were created during preparation.

See [self-hosted Langfuse activation and chat limits](docs/langfuse-self-hosted.md).
