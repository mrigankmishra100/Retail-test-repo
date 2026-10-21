# Retail + Distributor integration

## What is included

This branch is based on `R-I-Cloudworld` at
`b2698eefa6adcff596947771e499cb8313dea412`. It retains the existing Retail manager
and Supplier backend routes, agent clients and UI. Distributor runs **inside the
same backend process**; there is no new Distributor pod or HTTP forwarding to
the original backend.

The new role opens directly into Agent Chat. It has question suggestions,
answers, expandable sources, session-local conversations and sign-out. It has
no dashboard, inventory, requests, approvals, evaluation or agent registry.
The existing retail styles, layout, typography tokens, navigation, message
bubbles and composer are reused, not approximated by a separate theme.

Distributor RAG source comes from
`ORA-AIMLCOE/cloud-agnostic-rag`, branch `oci_app`, commit
`6afad41ec58c6c915ebd7cb55981b03e0deb3e14`. Its Python package is namespaced under
`backend/app/distributor_rag` to avoid overwriting the retail `app` package.
Its upstream public browser routes are **not mounted**. Its OCI providers,
retrieval workflow, citation validation, document ingestion and release logic
are integrated behind the retail session boundary. AWS deployment files,
credentials and private configuration were not imported.

Local adaptations include namespaced database tables, reuse of the existing
DB pool/wallet and OCI identity, optional existing guardrails for input,
retrieved context and output, readiness checks, and controlled admission.
When the existing guardrail setting is enabled, a failed check stops processing;
this integration does not turn it off. Demo knowledge is visibly labelled.

## Role and API boundaries

`POST /sessions` accepts `distributor` without a supplier ID when enabled.
Sessions are issued and checked by the backend, using the existing POC session
mechanism. This is not production SSO or proof of a user's business identity.

Distributor can call only its chat endpoints and session read/sign-out routes:

| Backend route | Purpose |
| --- | --- |
| `GET /distributor/knowledge` | Read active knowledge readiness and demo label |
| `POST /distributor/questions/ask` | Ask `{ "question": "..." }`, maximum 4,000 characters |

The frontend calls these beneath its existing same-origin `/api/` proxy,
including hosted invoke prefixes. It never receives OCI or provider keys.
Existing business APIs still require Retail manager/Supplier sessions.
Distributor cannot select another document, upload documents, read other users'
question history, register agents or invoke business mutations.

Questions are independent. Session chat history is for the user's convenience;
it is not passed as conversational memory to the model. Generated Markdown is
rendered without executing HTML or loading remote images.

## Private configuration

Distributor is disabled by default, so the existing deployment configuration
continues to work. The future update flow must add these settings to the **new
backend's own private configuration object**, never modify the original object:

| Setting | Value |
| --- | --- |
| `DISTRIBUTOR_ENABLED` | `true` |
| `DISTRIBUTOR_LLM_MODEL_ID` | Approved answer model ID |
| `DISTRIBUTOR_LLM_ENDPOINT` | Trusted HTTPS OCI model API endpoint |
| `DISTRIBUTOR_LLM_FORMAT` | `generic`, `generic_astra`, `cohere`, `responses` or `chat_completions`, matching the model |
| `DISTRIBUTOR_EMBEDDING_MODEL_ID` | Approved embedding model ID |
| `DISTRIBUTOR_EMBEDDING_ENDPOINT` | Trusted HTTPS OCI embedding endpoint |
| `DISTRIBUTOR_EMBEDDING_DIMENSIONS` | Actual output dimension of that model |
| `DISTRIBUTOR_OBJECT_PREFIX` | Dedicated `distributor/.../` prefix; default `distributor/knowledge/` |

The backend reuses its existing OCI region, compartment, namespace, bucket,
authentication mode, Oracle credentials/wallet/pool, and configured retail and
supplier agent endpoints. Resource-principal mode does not load a user's key.
The original MCP/retail/supplier endpoint mappings must remain in the new
backend configuration. The new frontend points to the new backend.

The Distributor uses **native Oracle vector search**, not the retail Enterprise
AI File Search vector store. Therefore the embedding settings and prepared
Distributor knowledge cannot be inferred from the existing retail vector-store
ID. An embedding-model change is not reported as ready until knowledge is
indexed for that configuration. Model availability and permissions need live
validation; local tests do not prove them.

## Knowledge and database safety

No schema initialization, retail seeding, resource creation or deletion runs at
startup, login or chat. The integration uses seven separate tables:

`DISTRIBUTOR_DOCUMENTS`, `DISTRIBUTOR_ANSWERS`, `DISTRIBUTOR_INGESTION_LOCKS`,
`DISTRIBUTOR_RELEASES`, `DISTRIBUTOR_ACTIVE_KNOWLEDGE`, `DISTRIBUTOR_INDEX`,
`DISTRIBUTOR_CHUNKS`.

The repository includes operator/automation preparation helpers. These are
**not new end-user deployment steps**, and none were run against a live DB:

- `python -m app.distributor_setup`: read-only table plan.
- `python -m app.distributor_setup --apply`: explicit creation, only if none of
  these tables exist. Existing/partially created tables require review; they are
  never automatically adopted or overwritten. Oracle DDL commits immediately.
- `python -m app.distributor_rag.admin ingest <file> --version <version> --demo`:
  upload/index/verify/activate fictional demo knowledge using the merged
  backend configuration. Omit `--demo` only for approved non-demo material.
- `python -m app.distributor_rag.admin status`: inspect active knowledge.

The included `docs/distributor-demo-knowledge.md` is the upstream fictional demo
document, not actual carrier policies. No public document-upload UI is added.
Keep preparation and cleanup ownership tracked by the deployment orchestrator;
do not use the old empty-schema cleanup logic on a shared upgraded schema.

## Local verification

Backend tests block external network calls and use SQLite plus provider fakes.
They exercise the real RAG workflow, Oracle repository contracts, role checks,
configuration mapping, guardrail failures, citation handling and error paths.
Browser tests use mocked APIs, including existing Retail/Supplier regressions,
desktop/mobile layout, keyboard use and automated accessibility checks.

```text
python -m venv .venv
python -m pip install -e ./backend pytest
python -m pytest tests -q

cd frontend
npm ci
npm run build
npx playwright test --config playwright.distributor.config.js
```

Use the venv's Python after activation. The browser configuration uses local
Microsoft Edge. The Linux runtime lock is `backend/requirements.lock`; Windows
testing omits Linux-only `uvloop`. Runtime dependency versions are otherwise
retained, with SQLAlchemy/greenlet added for Distributor.

Verified locally on 2026-10-21:

- `pytest tests -q`: 675 passed, 9 subtests passed, 1 live Oracle test skipped.
- Production frontend build: passed.
- Playwright: all 85 tests passed, including Retail/Supplier regressions and
  Distributor desktop/mobile, keyboard and accessibility checks.
- Dependency consistency (`pip check`) and staged whitespace checks: passed.
- The shipped Python frontend proxy is exercised against the backend in-process,
  not only through browser API mocks.

Running bare `pytest` also collects an existing dashboard adapter test under
`deployment/dashboard` that needs the separate Deployment Studio test harness
(`test_retail_oneclick`); it is not a standalone Retail app test. Use the
`pytest tests -q` command above for this repository's app suite. No Linux Docker
image build or live cloud test is included in these local results.

## Before VM deployment

This source merge is **not** the dashboard's side-by-side deployment feature.
`deployment/source-build.json` still describes the existing five-service full
deployment contract. Do not pass this branch to that flow expecting only two
new applications.

The separate dashboard update flow must verify the completed base deployment,
build only backend/frontend, reuse the original three agent endpoints and
shared resource IDs, prepare private version-specific configuration, track
borrowed versus owned resources, protect parents during cleanup, and verify
both the original and new URLs. It must also prepare/validate Distributor
knowledge and the approved OCI model settings. No VM rollout, image activation,
live Oracle-vector test or live model/trace-delivery test has been performed as
part of this local merge.
