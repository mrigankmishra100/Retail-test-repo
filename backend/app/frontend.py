"""React static hosting and a same-origin backend proxy.

Deploy on Container Instances behind HTTPS. Supports OCI IAM or unsigned
backend calls. Anonymous browser access is an explicit opt-in for a network-
restricted no-auth deployment; otherwise a separate demo password is required.
OCI credentials never reach the browser.
"""

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
import logging
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit

import httpx
import requests
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.http_logging import HttpLoggingMiddleware
from app.utils.logger import configure_logging
from app.proxy_routes import integrated_target, session_headers
from app.runtime_config import JsonSettingsMixin

configure_logging(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
logger = logging.getLogger(__name__)


class FrontendSettings(JsonSettingsMixin, BaseSettings):
    backend_invoke_base: str = ""
    backend_auth_mode: str = "oci_iam"
    backend_api_contract: str = "scaffold"
    frontend_static_dir: str = "/app/static"
    frontend_username: str = "demo"
    frontend_password: SecretStr | None = None
    frontend_allow_anonymous: bool = False
    app_env: str = "production"
    model_config = SettingsConfigDict(extra="ignore")


@lru_cache
def resource_signer():
    import oci
    return oci.auth.signers.get_resource_principals_signer()


def prepare_upstream(method, url, body, content_type, auth_mode):
    # Only purpose-built headers are forwarded, never browser credentials/cookies.
    prepared = requests.Request(method, url, data=body, headers={
        "Content-Type": content_type, "Accept": "application/json"
    }).prepare()
    if auth_mode == "oci_iam":
        resource_signer()(prepared)
    return prepared


def create_frontend(config: FrontendSettings | None = None):
    config = config or FrontendSettings()
    static = Path(config.frontend_static_dir).resolve()
    base = config.backend_invoke_base.rstrip("/")

    @asynccontextmanager
    async def lifespan(app):
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise RuntimeError("BACKEND_INVOKE_BASE must be a valid upstream URL")
        if config.backend_auth_mode not in {"oci_iam", "none"}:
            raise RuntimeError("BACKEND_AUTH_MODE must be oci_iam or none")
        if config.backend_api_contract not in {'scaffold', 'integrated'}:
            raise RuntimeError('Unknown backend API contract')
        if config.backend_api_contract == 'integrated' and (
                config.backend_auth_mode != 'none' or not config.frontend_allow_anonymous):
            raise RuntimeError('Integrated session transport requires unsigned upstream and anonymous static access')
        if config.backend_auth_mode == "oci_iam":
            if parsed.scheme != "https" or "/hostedApplicationsIam/" not in parsed.path or not parsed.path.endswith("/actions/invoke"):
                raise RuntimeError("OCI IAM requires the copied HTTPS hostedApplicationsIam invoke base")
        elif config.app_env != "development" and parsed.scheme != "https":
            raise RuntimeError("Production unsigned backend mode requires an HTTPS upstream")
        if (config.frontend_allow_anonymous and config.app_env != "development"
                and config.backend_auth_mode != "none"):
            raise RuntimeError("Anonymous production access requires BACKEND_AUTH_MODE=none")
        if not config.frontend_allow_anonymous and (
                not config.frontend_password or len(config.frontend_password.get_secret_value()) < 16):
            raise RuntimeError("Set FRONTEND_PASSWORD to a secret of at least 16 characters")
        if not (static / "index.html").is_file():
            raise RuntimeError("Frontend static build is missing")
        if config.backend_auth_mode == "none":
            logger.warning("unsigned_backend_enabled", extra={"service": "retail-frontend"})
        if config.frontend_allow_anonymous:
            logger.warning("anonymous_frontend_enabled_restrict_network_access",
                           extra={"service": "retail-frontend"})
        app.state.ready = True
        logger.info("frontend_ready", extra={"service": "retail-frontend"})
        try:
            yield
        finally:
            app.state.ready = False

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ready = False
    security = HTTPBasic(auto_error=False)

    def browser_auth(credentials: HTTPBasicCredentials | None = Depends(security)):
        if config.frontend_allow_anonymous:
            return
        expected = config.frontend_password.get_secret_value() if config.frontend_password else ""
        valid = credentials is not None and secrets.compare_digest(
            credentials.username.encode(), config.frontend_username.encode()
        ) and secrets.compare_digest(credentials.password.encode(), expected.encode())
        if not valid:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": 'Basic realm="Retail demo"'})

    @app.get("/health")
    def health():
        return Response(media_type="application/json")

    @app.get("/ready")
    def ready():
        return Response(status_code=200 if app.state.ready else 503, media_type="application/json")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE"], dependencies=[Depends(browser_auth)])
    async def proxy(path: str, request: Request):
        # Restrict the proxy to actual backend routes; no arbitrary OCI forwarding.
        permitted = (request.method == "POST" and path in {"chat", "approve"}) or (
            request.method == "GET" and (path in {"health", "ready"} or re.fullmatch(r"cases/[A-Za-z0-9_-]+", path))
        )
        forwarded = {}
        target = path
        if config.backend_api_contract == 'integrated':
            target = integrated_target(request.method, path, request.url.query)
            forwarded = session_headers(request.headers)
        else:
            if not permitted:
                raise HTTPException(404, "Unknown API route")
            if request.url.query:
                raise HTTPException(400, "Query parameters are not supported on these routes")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise HTTPException(413, "Request body too large")
        try:
            prepared = await asyncio.to_thread(prepare_upstream, request.method,
                f"{base}/{target}", bytes(body), "application/json", config.backend_auth_mode)
            prepared.headers.update(forwarded)
            async with httpx.AsyncClient(timeout=httpx.Timeout(260, connect=10), follow_redirects=False) as client:
                upstream = await client.request(prepared.method, prepared.url,
                    content=prepared.body, headers=dict(prepared.headers))
            logger.info("upstream_completed", extra={"service": "retail-frontend", "status": upstream.status_code})
            return Response(upstream.content, status_code=upstream.status_code,
                            headers={**{name: upstream.headers[name] for name in
                                ('x-trace-id', 'x-api-version', 'www-authenticate') if name in upstream.headers},
                                'Cache-Control': 'no-store'},
                            media_type=upstream.headers.get("content-type", "application/json"))
        except Exception:
            logger.exception("upstream_failed", extra={"service": "retail-frontend"})
            raise HTTPException(502, "Backend unavailable") from None

    @app.get("/{path:path}", dependencies=[Depends(browser_auth)])
    def assets(path: str):
        target = (static / (path or "index.html")).resolve()
        if not target.is_relative_to(static) or not target.is_file():
            raise HTTPException(404, "Asset not found")
        return FileResponse(target)

    app.add_middleware(HttpLoggingMiddleware, service="retail-frontend")
    return app


app = create_frontend()
