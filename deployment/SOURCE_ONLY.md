# Deployment V4 source-only branch

## What is in this branch

- Public branch: `deployment-v4-source`.
- Upstream: `ORA-AIMLCOE/retail-inventory-agent`, `Deployment_v4`.
- Exact imported source commit: `2f61f6cb7035604a4e42c12f675b1585fca7e453`.
- The preceding `61750a8` commit has the same application source, before the image archive addition.
- This branch has independent history. No `docker-images/` folder, image binaries or LFS pointers are imported.
- Existing Dockerfiles and historical deployment records are retained for reference. Their old OCIR image URLs are not used by the new source-build recipe.
- Application logic and original configuration examples are unchanged. One obsolete test expecting an unauthenticated placeholder was updated to test the current authentication boundary.
- The separately deployed Langfuse backend integration is not in the upstream source snapshot. Rebuilding cannot reproduce code that was only supplied in its saved image.

## Run locally on Windows

Prerequisites: Python 3.12 and Node.js 22 with npm. Docker is needed only for image builds.
From a new checkout:

```powershell
git clone --single-branch --branch deployment-v4-source https://github.com/mrigankmishra100/Retail-test-repo.git retail-v4-source
cd retail-v4-source
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[dev]"
if (!(Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
$env:RETAIL_ENV_FILE = (Resolve-Path backend\.env).Path
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In a second terminal in the same checkout:

```powershell
cd frontend
npm.cmd ci
$env:API_PROXY_TARGET = "http://127.0.0.1:8000"
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173. Backend health is http://127.0.0.1:8000/health.
Stop each foreground process with Ctrl+C.

This starts the UI and API with optional AI and notifications disabled. It does NOT
supply inventory data, create a database, or make policy-dependent workflows work.
For live features, edit ignored `backend/.env` with your own database DSN/user/password,
wallet configuration if required, OCI profile and policy bucket. Configure Responses
endpoint/model/project and enable it only when those resources and permissions exist.
Use `ENTERPRISE_AI_API_MODE=responses` for the V4 AI workflow; the historical example's
`chat` setting must not be enabled with Responses.

Initialize a new empty application schema using `database/schema.sql` then
`database/seed.sql` through a reviewed Oracle SQL tool. Do not execute all migrations
after the full fresh schema. `scripts/setup_db.py` is an unimplemented placeholder.
Actual supplier policy PDFs are not included in this snapshot.

Do not use the historical `scripts/start-local-backend.ps1` for a new customer's
setup: it contains an old machine path and enables a shared notification topic.
The commands above leave notifications disabled and do not contact that topic.

The Linux `backend/requirements.lock` is used for container builds; it contains
Linux-specific wheels. Windows local setup uses the package's declared dependencies.

## Build all five images from source

Start Rancher Desktop with the Docker (moby) engine, or another Linux Docker engine.
Run from the repository root:

```powershell
docker build --platform linux/amd64 -f backend/Dockerfile.source --target backend -t retail-v4-source:backend .
docker build --platform linux/amd64 -f backend/Dockerfile.source --target retail-agent -t retail-v4-source:retail-agent .
docker build --platform linux/amd64 -f backend/Dockerfile.source --target supplier-agent -t retail-v4-source:supplier-agent .
docker build --platform linux/amd64 -f backend/Dockerfile.source --target mcp-server -t retail-v4-source:mcp-server .
docker build --platform linux/amd64 -f frontend/Dockerfile -t retail-v4-source:frontend .
```

These use public Python/Node base images, pinned Python dependencies, and the
checksum-verified official Oracle Instant Client download. No original customer
OCIR image is pulled. The four Python service targets share build stages and can reuse layers when Docker retains the cache; downloads may still be needed.
Build credentials and customer runtime values are never copied into the images.

## Deployment engine status

Root `manifest.json` describes the five-service **source-build plan** and intended
resource/environment bindings. `deployment_status: planned` is deliberate: the current
engine rejects it before any OCI resource creation. Do not remove this flag to force a run.

The full flow will be:

1. Fetch this branch and pin its Git commit.
2. Build four targets from `backend/Dockerfile.source` and the frontend Dockerfile.
3. Create the customer database, private wallet/policy buckets and notification topic.
4. Initialize only the newly created database schema; generate and privately distribute its wallet.
5. Generate distinct service keys; populate JSON environment maps and customer resource IDs.
6. Upload each image to the customer's OCIR.
7. Deploy MCP, Retail/Supplier agents, Backend, then Frontend; bind each downstream invoke URL.
8. Check infrastructure health AND real database/application readiness before claiming success.

Remaining engine work:

| Requirement | Current gap |
| --- | --- |
| Docker multi-stage targets | `image.target` in this planned manifest needs engine build/cache support |
| GenAI database networking | Current GenAI adapter rejects database recipes |
| Schema creation and seed | No implemented retail initializer; SQL/PLSQL needs a proper runner |
| Wallet distribution | Engine must generate/upload the wallet and bind its output fields |
| JSON environment maps | Nested `json_object` bindings need resolution support |
| Hosted invoke URLs | Agent validator expects OCI invoke paths, not just bare application hostnames |
| Supplier policies | Source contains only a README; real documents must be provided/uploaded |
| Live model chat | Customer model/project/access configuration is still required |

The current compartment's hosted application limit also needs resolving before
creating five new applications. Container Instances do not remove the database and
configuration requirements. Internal agent endpoint validation currently expects OCI
hosted invoke URLs, so directly swapping to container IPs is not supported by this plan.

`public: false` documents the non-entry service role; it does not make a NO_AUTH
hosted endpoint private. Existing application-level session and service-key checks remain.
No cloud deployment, database modification or notification is performed by preparing this branch.

## Branch from an older timestamp instead

Git branches point to commits. In a clone of the ORIGINAL repository, choose and inspect
a commit on the intended branch before creating a branch:

```powershell
git fetch origin Deployment_v4
git log origin/Deployment_v4 --date=iso-strict --format="%h %ad %s"
$commit = git rev-list -1 --before="2026-10-05T23:59:59+05:30" origin/Deployment_v4
git show --stat $commit
git switch -c deployment-v4-oct05 $commit
```

For the known snapshot before Docker archives were added, use commit `61750a8`.
A timestamp filter follows commit history, not the time code was actually deployed.
Pushing an old original branch includes its ancestor history. This public source-only
branch instead uses a fresh import to avoid publishing image/credential history.

## Verification on 2026-10-09

- 98 offline tests passed, plus 7 subtests (one dependency deprecation warning).
- React production build passed; built frontend served HTTP 200 locally.
- Backend API HTTP health startup passed without live OCI configuration.
- Source-built backend and retail-agent images completed; both passed /health and /ready
  in temporary containers with networking disabled and read-only filesystems.
- Oracle Thick native client initialized successfully in the backend image.
- Supplier-agent and frontend container builds failed on Python package download read
  timeouts from files.pythonhosted.org; the MCP image build was not reached. These
  container builds are not claimed as verified. Retry the listed source-build commands
  when Docker's package-download connectivity is stable.
- Manifest file paths/targets were checked. Deployment Lab correctly blocks this
  planned manifest before creating resources.
- No cloud resources were created or modified. No images were pushed to OCIR.
- No database-backed, live model or end-to-end five-service workflow was tested.
- All build/test processes started for this verification exited. The two built images
  remain only in the local Docker engine, not in this Git branch.
