# One-click deployment preparation

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
6. Upload policy files to the new bucket and grant only the required runtime access. An empty bucket is insufficient.

Ordering: MCP first; Retail and Supplier next; Backend after both agents; Frontend last. All images use port 8080 and image-defined startup. Exactly one service is the dashboard entry point. No-auth hosted endpoints are public; `public: false` does not make the other hosted endpoints private.

The original `docker-images/manifest.json` remains the archive inventory; the new root `manifest.json` is the application/resource plan. Deployment Lab accepts an executable `manifest.json` as an alternative filename to `app-deployment.json`, but rejects ambiguous folders containing both.
