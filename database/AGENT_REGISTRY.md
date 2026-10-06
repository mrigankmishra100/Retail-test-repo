# Agent registry POC

The **Agent registry** sidebar page is shared by both existing workspace roles.
Signed-in users can list agents, register Name, Role/type (`RETAIL`, `SUPPLIER`,
`CUSTOM`), Description, public OCI Hosted Application invoke URL, and Active or
Inactive status. Entries are inactive by default. This is an application table,
independent of the existing optional Enterprise AI managed registry integration.

## Apply the migration manually

No migration runs at startup. Nothing has been applied to a live database.

For an existing database, connect with SQLcl or SQL*Plus as the existing table
owner using your normal Oracle wallet/connection procedure. From the repository
root, run:

```sql
@database/migrations/004_agent_registry.sql
```

This creates only `AGENT_REGISTRY` and its indexes/constraints. Oracle DDL commits
implicitly. The script stops if objects already exist; inspect a partial run
before continuing. If the runtime uses a separate application user, grant it
`SELECT`, `INSERT`, and `UPDATE` on this table and expose it using the same
schema-resolution arrangement as the existing application tables. Readiness
uses `USER_TABLES`/`USER_INDEXES`, matching the current table-owner setup.

For a fresh database, `database/schema.sql` already includes these objects; do
not run migration 004 again after creating that schema. Existing migrations
001–003 and seed data are unchanged. `scripts/setup_db.py` remains the existing
placeholder; it is not a migration runner.

The detailed readiness endpoint (`/status/ready`, or `/api/ready` through the
integrated UI proxy) reports `checks.agent_registry`:

- `ready`: table, unique index, and registry SQL parse checks succeeded.
- `migration_required`: the table is absent; existing application readiness and
  configured agent calls continue. Registry management returns 503 until migrated.
- `unavailable`: database/schema check failed or the migration is incomplete.

## Routing and compatibility

For each existing remote Retail/Supplier call, the backend queries the active
entry for that type. If there is none, or migration 004 has not been applied, it
uses the existing `AGENT_ENDPOINTS` entry. Activation/deactivation takes effect on
the next call without restarting the backend. At most one Retail and one Supplier
entry can be active, enforced by an Oracle unique index, including concurrent
writes. Deactivate the current entry before activating its replacement (otherwise
the API returns 409). Multiple Custom entries may be active; none are invoked.

Keep the existing `AGENT_ENDPOINTS` and `AGENT_SERVICE_KEYS` configuration. This
POC overrides the destination of configured remote roles; it does not switch a
local-only deployment to remote mode or create service keys. A registered Retail
or Supplier application must implement the current signed agent contract and use
the matching existing role service key. Registering a URL does not verify its
reachability or contract. It is best to switch destinations between workflows,
because in-flight state may belong to the previous application.

URLs must match the existing public HTTPS OCI invoke URL format, for example:

```text
https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.example/actions/invoke
```

The existing transport adds `/internal/agent/invoke`. Custom paths, credentials,
ports, query strings, fragments, and whitespace are rejected. URLs are validated
again when read for routing. Database failures other than a missing table and
upstream agent failures are surfaced rather than silently rerouting or replaying
actions. Existing response validation, role checks, signatures, timeout and
no-redirect behavior still apply. Hosted Applications stay on `NO_AUTH_CONFIG`;
no OCI authentication configuration is added.

## API

All routes use the existing application bearer session, for either workspace role:

| Method | Path | Result |
| --- | --- | --- |
| GET | `/registry/agents?limit=20&offset=0` | `{items, has_more}`; maximum limit 100 |
| POST | `/registry/agents` | Create an entry; 201 |
| POST | `/registry/agents/{agent_id}/activate` | Activate; 200, or 409 if another entry is active |
| POST | `/registry/agents/{agent_id}/deactivate` | Deactivate; 200 |

Example registration body:

```json
{
  "name": "Retail agent",
  "agent_type": "RETAIL",
  "description": "Existing retail workflow application",
  "endpoint_url": "https://inference.generativeai.ap-hyderabad-1.oci.oraclecloud.com/20251112/hostedApplications/ocid1.generativeaihostedapplication.oc1.ap-hyderabad-1.example/actions/invoke",
  "active": false
}
```

Names are required (up to 120 characters), descriptions are optional (up to
1,000), and URLs are required (up to 2,000). Extra fields, including authentication
configuration and service keys, are rejected. The registry is intentionally
shared across both roles; existing business endpoints retain their original role
and supplier checks.

## Verification

From the repository root, run the backend tests with the existing Python
environment (set `RETAIL_ENV_FILE` to a nonexistent file for offline testing):

```powershell
$env:RETAIL_ENV_FILE = 'missing-test.env'
.\.venv\Scripts\python.exe -m pytest -q
```

From `frontend`, run `npm ci` and `npm run build`. The registry browser tests use
mock HTTP responses and never contact Oracle or a hosted agent:

```powershell
npm run test -- --config playwright.registry.config.js
```

Install Playwright Chromium if needed, or use an existing browser via
`$env:PLAYWRIGHT_CHANNEL = 'chrome'` (or `msedge`). Oracle SQL is covered by offline
adapter/bind and migration consistency tests; execution of the DDL and SQL against
Oracle must be verified in a separately authorized test database.

### Results from this implementation

- Backend: **75 passed, 1 pre-existing failure**, including **45 passing registry
  tests**. `tests/test_tools.py::test_tools_use_offline_service_placeholders`
  already failed before these changes because it invokes an MCP tool without an
  application session and expects a legacy placeholder response.
- Browser: **6 passed** in headless Chrome with mocked API responses, including
  both roles, registration/activation/deactivation, invalid URLs, conflicts,
  missing migration feedback and mobile layout. Desktop/mobile screenshots were
  also inspected.
- Production build: **passed** using the previously installed/cached Vite 8.3.0
  toolchain. The lockfile pins Vite 6.4.3; a clean `npm ci` was attempted but the
  network policy blocked npm downloads. Cached packages were restored offline;
  `package.json` and `package-lock.json` were not changed. Repeat `npm ci` and
  `npm run build` in an environment that can access the locked packages before
  release. The Vite 8/plugin combination emits deprecation warnings.
- No live Oracle connection, migration, hosted-agent invocation or deployment
  was performed during verification.

## Changed files

| Area | Added files | Existing files with additive wiring |
| --- | --- | --- |
| Backend | `backend/app/registry_api.py`, `backend/app/services/agent_registry.py` | `backend/app/api.py`, `backend/app/dependencies.py`, `backend/app/agents/remote.py`, `backend/app/proxy_routes.py`, `backend/app/oci/database.py`, `backend/app/services/readiness.py` |
| Frontend | `frontend/src/pages/AgentRegistry.jsx`, `frontend/src/pages/AgentRegistry.css` | `frontend/src/App.jsx`, `frontend/src/modules.js`, `frontend/src/api.js` |
| Database/docs | `database/migrations/004_agent_registry.sql`, `database/AGENT_REGISTRY.md` | `database/schema.sql`, `README.md` |
| Tests | `tests/test_agent_registry.py`, `frontend/tests/agent-registry.spec.js`, `frontend/playwright.registry.config.js` | None |

Existing untracked `backend/retail_inventory_agent.egg-info/` and
`frontend/README.md` were left untouched.
