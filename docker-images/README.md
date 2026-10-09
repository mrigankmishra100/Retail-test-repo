# Reviewed deployment image archives

The current manifest is the **retail-v4-20261009.4** notification-verification
release. These are prebuilt **linux/amd64 Docker-save archives**, stored with
Git LFS. Deployment Studio downloads and validates them, then uploads to OCIR
through Registry V2; Docker/Rancher Desktop is not required.

| Component | Current archive |
| --- | --- |
| Frontend (unchanged) | frontend-deployment-v4-chat-first-20261005.tar |
| Backend (Langfuse base retained) | backend-notification-checks-20261009.tar |
| Retail agent | retail-agent-notification-checks-20261009.tar |
| Supplier agent | supplier-agent-notification-checks-20261009.tar |
| MCP server | mcp-server-notification-checks-20261009.tar |

Each updated archive preserves the original published image's base layers and
adds only two files in one layer: the no-send notification probe module and the
modified service entrypoint that registers it. This is not a rebuild of the
Langfuse integration from repository source. The source baseline difference
still applies. The original archives are retained for rollback.

## Download and validation

With Git LFS installed, in this repository:

```powershell
git lfs install
git switch main
git pull --ff-only
git lfs pull --include="docker-images/*notification-checks-20261009.tar,docker-images/frontend-deployment-v4-chat-first-20261005.tar"
```

A file beginning `version https://git-lfs.github.com/spec/v1` is an LFS pointer,
not the actual archive. GitHub LFS storage/bandwidth allowances apply. Archive
hashes, byte sizes, image tags and normalized Registry V2 manifest digests are
in [manifest.json](manifest.json). Archive hashes and registry digests differ.

The paired dashboard must also use recipe **retail-v4-20261009.4**. Older
recipes intentionally reject changed images. Do not bypass the manifest checks.

## Notification behavior

`GET /status/notifications` performs bounded, cached GetTopic and ListSubscriptions
checks using the service's configured resource principal. It exposes only status,
counts, configuration flags and a topic hash: no credentials or email addresses.
It never calls PublishMessage. A pending subscription is not an IAM denial;
the recipient must confirm the OCI email before business dispatch can work.

Deployment Studio enables notifications by default, supplies generated private
configuration and reports runtime access separately from subscription readiness.
IAM still must grant topic read/publish and subscription inspection. Successful
GET checks do not prove publishing permission or email delivery. Approval and
explicit dispatch remain required for business messages.

No OCI signing keys, database passwords, wallets or runtime JSON configuration
are included in this release. Original public base archives were hash-verified;
all layer diff IDs were verified by the deployment parser. This is not a full
vulnerability scan or a fresh end-to-end cloud deployment verification.

See [release provenance](../deployment/notification-checks-20261009.json) for the
base/new archive hashes and the exact changed paths. Existing historical cloud
deployment records are retained as history, not rewritten to claim this release
is already deployed everywhere.
