FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/src ./src
COPY frontend/public ./public
COPY frontend/index.html frontend/vite.config.js ./
# Deployment branch excludes local test artifacts; build the complete React UI.
RUN npm run build

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
ARG RELEASE_TAG=deployment-v2-20260918
ARG SOURCE_REVISION=uncommitted
LABEL org.opencontainers.image.version=${RELEASE_TAG} org.opencontainers.image.revision=${SOURCE_REVISION}
ENV APP_RELEASE_TAG=${RELEASE_TAG}

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
    OCI_AUTH_MODE=resource_principal

WORKDIR /app
COPY backend/requirements.lock /app/requirements.lock
RUN pip install -r requirements.lock
COPY backend/app /app/app
COPY backend/pyproject.toml /app/pyproject.toml
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
COPY --from=build /app/dist /app/static
ENV BACKEND_AUTH_MODE=none FRONTEND_STATIC_DIR=/app/static BACKEND_API_CONTRACT=integrated

USER appuser
CMD ["python", "-m", "uvicorn", "app.frontend:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]

EXPOSE 8080
