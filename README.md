# Retail Inventory Agent — V5 deployment test

V5 application source from `ORA-AIMLCOE/retail-inventory-agent` commit
`d8d38dc122cbab84b3bf29b3a0ee37513a688335`, adapted for the existing Deployment
Studio source-build contract. The public branch retains its public history;
private repository history, credentials and runtime configuration are not imported.

Use this source URL in the one-click dashboard and select all five services:

https://github.com/mrigankmishra100/Retail-test-repo/tree/Retail-Inventory-Demo

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
