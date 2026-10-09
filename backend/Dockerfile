# OCI Generative AI Hosted Applications require a Linux/amd64 image.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# Refresh this build argument for each security release so cached layers do not
# skip Debian updates. Keep package metadata for vulnerability scanners.
ARG OS_SECURITY_REFRESH=20260916-v1.1
RUN echo "Applying Debian updates: ${OS_SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/tmp/cache \
    FASTMCP_HOME=/tmp/fastmcp \
    APP_ENV=production \
    OCI_AUTH_MODE=resource_principal \
    HOSTED_PROBE_MODE=true

WORKDIR /app

COPY backend/requirements.lock /app/requirements.lock
RUN pip install -r requirements.lock
COPY backend/app /app/app
COPY backend/pyproject.toml /app/pyproject.toml
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8080

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--log-level", "info"]
