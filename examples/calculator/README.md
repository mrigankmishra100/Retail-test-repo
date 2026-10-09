# Little Calculator

The same calculator source supports both Deployment Lab targets. It serves a browser UI, `/api/calculate`, `/health` and `/ready`; no database or application secrets are required.

## Deploy from GitHub

- Repository: https://github.com/mrigankmishra100/Retail-test-repo
- Branch: main
- Application folder: examples/calculator
- Source commit: leave blank to fetch the updated branch. An old pinned commit will still contain the old image inputs.
- Workflow: Automatic app + resources

Choose **Generative AI Applications** for a no-auth application with managed networking. The image starts on `0.0.0.0:8080`; runtime port/command overrides are not applied. The engine uploads to OCIR, creates the app and active deployment, polls status, and shows the hostname OCI returns. No customer VCN/subnet or compartment is created for this target. Runtime IAM still needs private OCIR access; it is separate from app login.

Choose **OCI Container Instances** to retain the existing deployment route and selected network mode. Its optional port/command override can continue using 8003. Both `/health` and `/ready` work on the configured listening port.

Run readiness before Deploy. Docker/Rancher must be running for a fresh local build. The changed server and recipe invalidate the old image cache; retries of the same new commit may reuse the verified new image. Existing OCI deployments/images are not changed by publishing this source update.

The no-auth GenAI configuration was observed on existing apps in this tenancy, but a new calculator hosted deployment and frontend/static-asset delivery still need a live test. ACTIVE status alone does not verify the full UI.

## Build image

The Dockerfile uses the official Python image mirrored in ECR Public to avoid the Docker Hub connection issue previously encountered on this machine. TLS verification stays enabled. The app still deploys to OCI, and no AWS account is needed.
