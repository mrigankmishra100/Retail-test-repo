# Retail + Distributor integration

## What is included

`retail-distributor-merged` combines the integration branch at `a0119e2` with
`ai-world-retail-demo` at `7a9d3e9`; their shared Cloudworld base is
`b2698eefa6adcff596947771e499cb8313dea412`. It retains the Distributor implementation
and incorporates the latest Retail/Supplier delivery routing, OpenAI model picker,
comparison fixes and UI updates. Existing role and backend boundaries are retained. Distributor runs **inside the
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
| `DISTRIBUTOR_VECTOR_STORAGE` | `json` for the existing Oracle 19c demo; `native` (default) only for databases supporting Oracle VECTOR |
| `DISTRIBUTOR_OBJECT_PREFIX` | Dedicated `distributor/.../` prefix; default `distributor/knowledge/` |

The backend reuses its existing OCI region, compartment, namespace, bucket,
authentication mode, Oracle credentials/wallet/pool, and configured retail and
supplier agent endpoints. Resource-principal mode does not load a user's key.
The original MCP/retail/supplier endpoint mappings must remain in the new
backend configuration. The new frontend points to the new backend.

The Distributor uses its **own database-backed knowledge index**, not the retail
Enterprise AI File Search vector store. Therefore the embedding settings and prepared
Distributor knowledge cannot be inferred from the existing retail vector-store
ID. An embedding-model change is not reported as ready until knowledge is
indexed for that configuration. Model availability and permissions need live
validation; local tests do not prove them.

### Existing Oracle 19c database

The approved local adapter uses `DISTRIBUTOR_VECTOR_STORAGE=json`. Embeddings
are JSON arrays in a CLOB column with an `IS JSON` constraint. The backend reads
only chunks matching the document, owner and embedding-index identity, then
calculates exact cosine similarity and returns the best matches. It does not
require a database upgrade, native VECTOR functions or another service.

This is deliberately bounded to a small demo knowledge set: at most 1,500
chunks per document/index. Oversized knowledge, invalid vectors and dimension
mismatches fail rather than silently dropping data. Storage mode participates
in the index identity, so a native index cannot be mistaken for a JSON index.
This is not an automatic conversion of an existing Distributor schema.

Retail/Supplier tables and rows are not changed by Distributor setup. Both
versions of the Retail/Supplier app still share their existing business data,
as requested. Do not use legacy empty-schema cleanup against the shared schema.

The storage format follows Oracle's [19c JSON storage documentation](https://docs.oracle.com/en/database/oracle/oracle-database/19/adjsn/overview-of-storage-and-management-of-JSON-data.html).

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

Merged release validation on 2026-10-23:

- Full application suite: 730 tests and 23 subtests passed; one live Oracle test
  skipped because no explicit test-schema opt-in was provided.
- Combined browser suite: 94 tests passed, covering Retail/Supplier workflows,
  registry, OpenAI selection, comparisons, Distributor role isolation, source
  rendering, desktop/mobile layout and accessibility.
- Production frontend build and the reviewed dashboard's five-service settings,
  routing and hosted registry proxy preflight passed with synthetic credentials.
- Distributor runtime implementation and dependency pins remain unchanged from
  `a0119e2`; AI World chat/comparison logic remains unchanged from `7a9d3e9`.
- No new cloud deployment, image builds or live knowledge ingestion were run.


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

Historical integration-branch validation on 2026-10-21:

- With the Oracle 19c adapter: 692 application tests and 9 subtests passed;
  1 live Oracle test was skipped.
- Production frontend build: passed.
- Playwright: all 85 tests passed, including Retail/Supplier regressions and
  Distributor desktop/mobile, keyboard and accessibility checks.
- Dependency consistency (`pip check`) and staged whitespace checks: passed.
- The shipped Python frontend proxy is exercised against the backend in-process,
  not only through browser API mocks.

Running bare `pytest` also collects an existing dashboard adapter test under
`deployment/dashboard` that needs the separate Deployment Studio test harness
(`test_retail_oneclick`); it is not a standalone Retail app test. Use the
`pytest tests -q` command above for this repository's app suite.

Additional local Ashburn preflight on 2026-10-21:

- The five pinned `R-I-Cloudworld` images built on local Rancher Linux/amd64.
- The two merged backend/frontend images, including the unpushed Oracle 19c
  adapter, built and passed offline settings/platform/archive checks. Local
  changes are recorded with a separate source-overlay hash, not represented as
  already present in the GitHub base commit.
- Small synthetic requests to `google.gemini-2.5-flash` and `cohere.embed-v4.0`
  (1,536 dimensions) passed using the selected local Ashburn OCI identity.
- These checks are not a successful hosted deployment or end-to-end Distributor
  test. No application schema changes or cloud resource deletions were performed.
- The subsequent live SQL compatibility check could not connect to the configured
  database service (`ORA-12514`). Do not treat the mocked schema tests as live
  Oracle setup/ingestion verification; restore a reviewed database target first.

## Before VM deployment

For this merged release, deploy the five-service `ai-world-retail-demo` release
at `7a9d3e9` first, then select `retail-distributor-merged` for the side-by-side
backend/frontend update. Configure OpenAI on the reused Retail agent; Distributor
continues using its separate OCI settings. The [deployment handoff](ai-world-deployment-handoff.md)
explains the private key and first-stage version requirement.

This source merge is **not** the dashboard's side-by-side deployment feature.
`deployment/source-build.json` still describes the existing five-service full
deployment contract. Do not pass this branch to that flow expecting only two
new applications.

The separate dashboard update flow must verify the completed base deployment,
build only backend/frontend, reuse the original three agent endpoints and
shared resource IDs, prepare private version-specific configuration, track
borrowed versus owned resources, protect parents during cleanup, and verify
both the original and new URLs. It must also prepare/validate Distributor
knowledge and the approved OCI model settings. Local model access does not prove
the new applications' resource-principal permissions. No VM rollout, image
activation, live Distributor schema/ingestion test or trace-delivery test has
been performed as part of this local merge.
