# Little Calculator

A visible calculator webpage with a Python API. No third-party Python dependencies, databases, buckets, notification topics or app secrets are needed.

## Deploy with Deployment Lab

- Workflow: Automatic app + resources
- GitHub repository: https://github.com/mrigankmishra100/Retail-test-repo
- Branch: main
- Application folder: examples/calculator
- Provide your initial OCI/OCIR credentials and have a Docker-compatible Linux builder running, then select Deploy application.

The deployment engine reads app-deployment.json. It creates a new child compartment, VCN/subnet, internet gateway, routing/security rules, OCIR repository and one container instance. This example does not request databases, buckets, notification topics or application IAM policies. It receives the OCI region automatically as DEPLOYMENT_REGION.

When healthy, use Open application in the dashboard. The webpage supports addition, subtraction, multiplication and division, with recent calculations kept only in page memory. Calculations go to the Python server, so this checks frontend-to-server connectivity. Decimal results use up to 28 significant digits.

GET /health returns service readiness and the configured region. The prototype URL uses HTTP on port 8080. No live OCI deployment is included with these files.

## Local preview

Run `python server.py --host 127.0.0.1 --port 8790`, then open http://127.0.0.1:8790.

## Base image source

The Dockerfile downloads the official Python image from Docker's verified Amazon ECR Public repository: https://gallery.ecr.aws/docker/library/python. This avoids the Docker Hub download endpoint that currently fails certificate verification on this machine. TLS verification stays enabled. No AWS account is needed; the built application image is still uploaded to OCIR and deployed on OCI.
