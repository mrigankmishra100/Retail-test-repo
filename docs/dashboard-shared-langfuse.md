# Shared Langfuse for a VM-hosted deployment dashboard

The deployment dashboard and the existing Langfuse service share the VM at
150.136.235.10 in the owner's tenancy. Users open the dashboard in their browser
and supply their own OCI account configuration. No additional Langfuse server
is created per user. The deployed retail services connect to the shared host
using its reachable address, not localhost.

The public retail images contain the tracing SDK and read project credentials
from private runtime JSON. They contain no project key defaults. The dashboard
operator configures the common Langfuse project once on the **dashboard VM**.
This integration is an installation step for that VM; publishing this repository
does not automatically modify somebody else's dashboard installation.

## Install once on the dashboard VM

The operator should first finish or pause deployments and cleanup jobs. Take a
backup of the installed dashboard. From a checkout of this public branch on the
VM, use the dashboard's Python interpreter (adjust these example paths):

```bash
/opt/deployment-dashboard-runtime/venv/bin/python scripts/install_dashboard_langfuse.py --dashboard-root /home/opc/deployment-dashboard
/opt/deployment-dashboard-runtime/venv/bin/python scripts/install_dashboard_langfuse.py --dashboard-root /home/opc/deployment-dashboard --apply
```

The first command validates without editing. The second installs a small helper
and hooks in `retail_workflow.py` and `retail_recipe.py`. It preserves backups,
retains unrelated code, is idempotent, and rejects unfamiliar source. It does not
restart the dashboard, deploy anything, or read credentials. The hook supports
the reviewed dashboard source at `5162b3e` as well as the earlier workflow shape.
Recheck/reapply it after a dashboard upgrade; it is not an upstream dashboard commit.

Create an operator-only file outside Git and image build contexts, for example
`/home/opc/.config/deployment-dashboard/langfuse.json`. Copy the shape from
`deployment/dashboard/langfuse.example.json`, replacing the key placeholders
privately with the shared project's keys. The dashboard service user must be able
to read it; on Linux its file permissions must be `0600`, owned by that user or
root, with the parent directory restricted to `0700`. Do not use a symlink.

Set this environment variable on the dashboard backend service:

```ini
[Service]
Environment="STUDIO_LANGFUSE_CONFIG_FILE=/home/opc/.config/deployment-dashboard/langfuse.json"
```

For systemd, store that in a service drop-in, run `sudo systemctl daemon-reload`,
then restart the dashboard backend **when no deployment or cleanup job is running**.
The variable contains only a local path. The file itself contains the keys.

## What happens during one-click deployment

1. When a new deployment is accepted, the dashboard reads its administrator-owned
   file, validates it, and saves a copy in that run's existing encrypted private
   record. Missing/invalid configured files stop creation with a sanitized error.
2. The recipe adds these settings to Backend, Retail Agent, Supplier Agent and MCP
   Server private configuration objects. Existing database settings, service keys
   and generated URLs remain unchanged. Frontend configuration receives no keys.
3. OCI hosted services read their own private configuration on startup through the
   existing Object Storage bootstrap and export traces to the shared Langfuse host.

All participating users share this Langfuse project. Their OCI credentials and
application resources remain specific to their deployment. The Langfuse host must
be reachable **from the deployed services**, not just from the dashboard VM or a
user's laptop. A difference in tenancy alone does not establish or prevent that
connectivity; routing and firewall rules must allow it.

Changing the operator file affects new runs only. Resuming an existing run reuses
its saved snapshot so configuration checksums and recovery expectations stay stable.
Runs created before installation are not modified; start a new source inspection
and deployment to include the hook and latest public app commit. A missing
`STUDIO_LANGFUSE_CONFIG_FILE` leaves the previous recipe behavior unchanged, and
the public app defaults to tracing disabled. A JSON file with only
`{"LANGFUSE_TRACING_ENABLED": false}` explicitly disables tracing for new runs.

## Verify after deployment

In the retail app, open **Evaluation & metrics > Cloud monitoring > Langfuse
tracing > Open Langfuse**. Sign into the shared Langfuse project, send a new chat
question, and look for the resulting trace. Configured status is not proof that
an event reached the server. The supplied synthetic diagnostic can independently
verify authentication and observation readback using a private dotenv input.

This repository's tests use synthetic keys, no live model/database requests, and
no production deployment. Installing the hook still requires access to the correct
dashboard VM or help from its operator.
