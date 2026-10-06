# Deployed image archives

These five **linux/amd64** Docker save archives match the active OCI image tags
checked on **2026-10-06**. Total archive size: **1,398,263,808 bytes** (~1.4 GB).
Files are stored through **Git LFS**, not as ordinary Git blobs. Fetching them
uses the repository's LFS storage/bandwidth allowance.

| Component | Image tag |
| --- | --- |
| Frontend | deployment-v4-chat-first-20261005 |
| Backend (includes Langfuse) | langfuse-backend-20260922-v1 |
| Retail agent | deployment-v2-20260918 |
| Supplier agent | deployment-v2-20260918 |
| MCP server | deployment-v2-20260918 |

The different tags are intentional: V4 updated the frontend image and the
Retail/MCP runtime configs, not all five images. No images were rebuilt for this
archive upload. The Langfuse backend archive is the actual deployed binary;
the repository's backend source remains the V2 baseline.

## Download

From your local repository checkout with Git LFS installed:

```powershell
git lfs install
git switch Deployment_v4
git pull --ff-only
git lfs pull --include="docker-images/*.tar"
git lfs ls-files
```

A small text file beginning `version https://git-lfs.github.com/spec/v1` is an
LFS pointer, not the image. Run `git lfs pull` to download the real archive.

## Load into Rancher Desktop / Docker

Start Rancher Desktop with its Docker-compatible engine, then run these commands
from the repository root:

```powershell
docker load --input docker-images/frontend-deployment-v4-chat-first-20261005.tar
docker load --input docker-images/backend-langfuse-backend-20260922-v1.tar
docker load --input docker-images/retail-agent-deployment-v2-20260918.tar
docker load --input docker-images/supplier-agent-deployment-v2-20260918.tar
docker load --input docker-images/mcp-server-deployment-v2-20260918.tar
docker image ls
```

Loading images does not start containers or deploy anything to OCI. The original
`hyd.ocir.io/ax4qsxvnsmtm/retail-inventory/...` tags are preserved. You still need
the private runtime configuration, OCI identity/IAM, database access and policies.
Private configuration files and credentials are **not** supplied in this folder.

See [manifest.json](manifest.json) for archive SHA-256 checksums, byte sizes and
registry digests; an archive checksum is different from a registry image digest.
Verify a downloaded file with `Get-FileHash -Algorithm SHA256 <archive-path>`.
The layer scan checked known local secrets and credential-file/private-key
indicators, with no findings. It is not a full security or vulnerability audit.

See [deployment-v4-release.json](../deployment/deployment-v4-release.json) for
runtime config object names, deployment verification and rollback references.
