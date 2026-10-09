# One-click deployment preparation

## Which manifest does the deployment engine use?

| Workflow | File | Current meaning |
| --- | --- | --- |
| Automatic app + resources, repository root | `manifest.json` | Retail application/resource plan; deliberately blocked in the current local Deployment Lab |
| Saved-image discovery | `docker-images/manifest.json` | Current archive filenames, sizes, checksums and image release; no complete resource orchestration |
| Paired Deployment Studio | `docker-images/manifest.json` plus Studio recipe `retail-v4-20261009.4` | Release-specific orchestration belongs to that separate matching engine; its implementation is not in this repository or the inspected local test-dashboard |
| Calculator, application folder `examples/calculator` | `examples/calculator/app-deployment.json` | Executable calculator recipe |
| Historical deployment records | `deployment/active-images.json`, `deployment/deployment-v2-images.json`, `deployment/deployment-v4-release.json` | Record previous deployments; do not use them as the current image inventory |

The root plan is synchronized to `retail-v4-20261009.4`: all five archive paths and
SHA-256 values match `docker-images/manifest.json`. The four updated service hashes
also match `deployment/notification-checks-20261009.json` and the tracked Git LFS
pointer metadata. This verifies metadata consistency, not archive download availability
or a live deployment.

The main-branch plan uses **saved images**, not Dockerfile builds. Rebuilding source
requires a separate supported source-build recipe and cannot recreate the backend's
image-only Langfuse integration. Do not substitute source images while retaining
saved-archive checksums. The `deployment-v4-source` branch documents that separate plan.

Release alignment does not make the root plan executable. Keep `deployment_status`
as `planned` until the missing engine capabilities below are implemented and tested.
Do not add a second root `app-deployment.json`; the current engine rejects two recipes
in the same application folder.

## Calculator: test this first

Use repository `Retail-test-repo`, branch `main`, application folder `examples/calculator`, and leave Source commit blank to fetch the updated source. Choose Generative AI Applications in Deployment Lab, run readiness, then deploy. The same source remains compatible with Container Instances. This update adds `/ready`, keeps `/health`, and declares the readiness path in `app-deployment.json`. It does not update a running OCI deployment.

## Retail: planned manifest, not deployable yet

The root `manifest.json` describes all five services using the checksum-pinned saved images already in `docker-images`. This is a **planned manifest**, not an executable version-1 recipe. The dashboard recognizes `deployment_status: planned` and reports its blockers before any image build, OCI connection or resource creation. Do not remove that marker to force deployment.

The plan captures a customer-owned Autonomous Database, private wallet and policy buckets, a notification topic, fresh-schema initialization, four generated service/delegation keys, dependency order and environment references. It contains no existing customer credentials, resource IDs or URLs. Notifications are disabled until an intended audience/subscriptions are configured. Model-assisted chat/vector search are explicitly disabled in this infrastructure-first plan; the optional AI section lists the remaining customer-specific setup.

Required next-phase engine work:

1. Database connectivity for GenAI managed/custom networking, including the wallet and runtime IAM model. The current GenAI adapter rejects database recipes.
2. Create a new schema and execute `database/schema.sql` then `database/seed.sql` safely. `scripts/setup_db.py` is currently a placeholder. The fresh schema includes migrations; running migrations 001-004 again can fail or duplicate work.
3. Generate a wallet, upload it privately, and resolve wallet settings for the four database-using services.
4. Resolve nested JSON maps for `AGENT_ENDPOINTS`, `AGENT_SERVICE_KEYS`, and `REMOTE_MCP_SERVICE_KEYS`. `json_object` is a planned binding shape, not currently executable.
5. Supply `services.<name>.invoke_url` in the exact OCI format accepted by `validate_agent_endpoint`; the current dashboard only returns application hostname URLs. Do not weaken the retail endpoint validator to accept arbitrary hosts.
6. Upload policy files to the new bucket and grant only the required runtime access. An empty bucket is insufficient. The repository currently contains only `policies/README.md`; actual supplier policy documents must also be supplied.

Ordering: MCP first; Retail and Supplier next; Backend after both agents; Frontend last. All images use port 8080 and image-defined startup. Exactly one service is the dashboard entry point. No-auth hosted endpoints are public; `public: false` does not make the other hosted endpoints private.

The original `docker-images/manifest.json` remains the archive inventory; the new root `manifest.json` is the application/resource plan. Deployment Lab accepts an executable `manifest.json` as an alternative filename to `app-deployment.json`, but rejects ambiguous folders containing both.

## Notification release and runtime settings

The October 9 images add `/status/notifications`. This no-send diagnostic checks
topic visibility and subscription state. It can return 503 when subscribers are
missing even though the service itself is healthy, so the manifest retains `/health`
and `/ready` for startup probes. Passing those probes does not prove database
readiness, business workflow success or email delivery.

Notification publishing and optional AI remain disabled in the root plan until
customer configuration is supplied. The separately paired Studio described in the
release notes supplies its own enabled flags/private JSONs. These are different
configuration contracts; merely matching image versions does not make the local
Deployment Lab perform those Studio steps.

Customer IDs, passwords, wallet settings, service keys and endpoints must be
resolved at runtime. Historical bootstrap/configuration scripts reference an
existing deployment; they are not a generic customer provisioning recipe.
