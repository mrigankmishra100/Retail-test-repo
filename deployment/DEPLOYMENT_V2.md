# Deployment v2: coordinated release

Release tag for all five application repositories: `deployment-v2-20260918`.
Source branch: `deployment_v2`, including all feature commits through `0b9644f`
and the release diagnostics/regression fixes. No intermediate application release
tags are required. Published digests are recorded separately after verification.

## Included together

- Full React UI: registry, revised workspaces, replenishment/approval screens,
  notification status and chat presentation.
- Backend APIs, durable cases, deterministic pricing, explicit approvals and
  notification outbox. The signed request/resume-body fixes are retained.
- Separate Retail and Supplier workflow applications.
- Application Agent Registry in Oracle, with dynamic routing, configured endpoint
  fallback, and registration/activation available to both existing roles as requested.
  This is not the managed OCI Agent Registry service.
- OCI Responses for enabled model calls, including Retail advice/chat, function
  selection and optional notification prose. There is no native Chat fallback.
  Supplier calculations, SQL execution and notification delivery are deterministic
  services, not independent model calls.
- OCI Conversations plus session-owned Oracle chat history. The OCI conversation
  mapping and session/replay state still use the existing cache; this is not full
  multi-replica session recovery.
- Private PDF Object Storage, policy parsing, OCI file search and vector retrieval.
- Remote MCP with authenticated, body-bound delegation and role/approval checks.
- Optional `query_retail_data`: model-selected, approved SELECT templates for
  inventory, sales summaries and supplier options. Not OCI-native arbitrary NL2SQL.
- OCI Notifications with reviewable formatted drafts and unchanged approval gates.
- Private JSON configuration loading from Object Storage, preserving the existing
  five bootstrap variables and existing matching service keys.

## Before activating the images

1. Inspect the existing database tables. Apply only missing migrations as the
   current table owner: `database/migrations/003_chat_persistence.sql`, then
   `database/migrations/004_agent_registry.sql`. These create chat and registry
   tables; DDL commits implicitly. Stop and inspect if partially applied. Do not
   run the full schema or seed scripts over an existing database.
2. Preserve the current private JSON files, endpoints and matching keys. To use
   the optional query tool, set `ENTERPRISE_AI_NL2SQL_ENABLED` to JSON boolean
   `true` in both `retail-agent-v1.json` and `mcp-v1.json`. Both already need the
   configured OCI AI endpoint. No additional container environment variable is
   needed. If disabled, named inventory tools remain available.
3. Retain `ENTERPRISE_AI_API_MODE=responses` wherever model calls are enabled,
   the existing project/model/key and file-search/vector-store settings. Keep
   Conversations enabled on Retail. Backend/Supplier can retain disabled model
   settings; their non-AI workflows still run. Notification narratives use
   Responses only where that service's existing configuration enables it;
   otherwise deterministic prose remains available.
4. Do not enable the unimplemented managed guardrails/MCP-safety/registry flags
   merely because similarly named local POC features exist.
5. Activate the matching release on MCP first, then Retail/Supplier, then Backend
   and Frontend during a controlled cutover. New agents send argument-digest
   headers; the new MCP accepts old and new signed formats. Avoid active workflow
   switches and retain action idempotency keys. Use the same release across all
   five apps, rather than mixing feature revisions.
6. Check health and detailed backend readiness. Verify chat history, data tools,
   PDF retrieval, registry routing and the approval flow. Sending a notification
   requires the normal explicit approval; health checks do not send one.

No database migration, cloud JSON update or Hosted Application activation is
performed by building/pushing these images. The old `active-images.json` remains
the rollback/deployed record until the new applications are actually activated.

## Diagnostics

Look for `application_features_configured`, `agent_registry_completed`,
`agent_registry_route`, `agent_registry_failed`, `mcp_remote_stage`,
`mcp_remote_failed`, `mcp_tool_failed`, `chat_history_failed`,
`retail_chat_tool_completed`, `retail_conversation_incomplete` and
`notification_narrative_completed`. Correlate `trace_id`, MCP `call_id`, stage,
duration, status and OCI request IDs. Release labels identify the source commit.
Logs omit prompts, chat bodies, policy contents, credentials and exception text;
safe stack locations are retained. Failed tools are not labelled verified answers.

## Build paths

Use `backend/Dockerfile.split` for the four service targets and the full
`frontend/Dockerfile` for an online React/runtime rebuild. For the current offline
release, `frontend/Dockerfile.release` runs a fresh `npm ci --offline` from cached
lockfile packages, rebuilds all React source, and copies the new UI and Python
code onto the verified current frontend runtime. It does not reuse old UI assets.
The cached UI dependency image must exist locally; the normal Dockerfile is the
alternative on a machine with dependency-network access.

All production builds use `--platform linux/amd64 --provenance=false --sbom=false`.
Inspect remote manifests after push. Source tests and browser tooling are not
copied into the application runtime images.

## Verification before packaging

- 22 offline release regression tests passed in the backend runtime.
- Six headless browser tests passed against freshly built React assets, covering
  both roles, registration/activation, conflicts, missing migrations and mobile.
- All 83 application Python files parsed/imported successfully.
- All five packaged applications passed offline startup/health/authentication
  checks, with all 83 packaged source files matching the release source.
- All five images were pushed and remotely verified as single Docker schema-v2
  manifests for linux/amd64. See `deployment-v2-images.json` for exact digests.
- Fresh `npm ci --offline` installed 97 packages from the lockfile; Vite 6.4.3
  built all 38 UI modules. The clean online npm attempt was cancelled after
  dependency-network delays.
- The full legacy pytest suite was not executed: pytest is absent from the
  runtime and the earlier dependency downloads failed. These release checks do
  not establish live Oracle/OCI integration success; verify after migration and
  activation. No cloud workflow was invoked during offline tests.
