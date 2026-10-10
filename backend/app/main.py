"""FastAPI application entry point."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.config import settings
from app.dependencies import get_container
from app.utils.logger import configure_logging
from app.http_contract import install_http_contract, ERROR_RESPONSES
from app.contracts import ReadinessResponse, HealthResponse
from fastapi.responses import JSONResponse
from app.mcp.server import mcp_http_app
from app.utils.http_logging import HttpLoggingMiddleware

logger = logging.getLogger(__name__)

if settings.application_role != 'backend':
    raise ValueError('Agent applications must use the role-isolated agent entry point')


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Configure local process concerns without contacting external services."""
    configure_logging()
    logger.info("Retail Inventory Agent API starting")
    try:
        async with mcp_http_app.lifespan(application):
            application.state.ready = True
            yield
    finally:
        application.state.ready = False
        application.state.container.close()
        logger.info("Retail Inventory Agent API stopped")


app = FastAPI(title="Retail Inventory Agent POC", version="1.0.0", lifespan=lifespan)
app.state.ready = False
install_http_contract(app)
app.add_middleware(HttpLoggingMiddleware, service="retail-api")
app.state.container = get_container()
from app.oci.studio_notification_probe import install_fastapi
install_fastapi(app, lambda: app.state.container.notifications)
app.state.enterprise_ai_registry = app.state.container.registry
app.state.langsmith = app.state.container.langsmith
app.state.langfuse = app.state.container.langfuse
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-ID", "X-API-Version"],
)
app.include_router(router, responses=ERROR_RESPONSES)
app.mount("/mcp", mcp_http_app)


@app.get("/health", response_model=HealthResponse)
def health() -> dict[str, str]:
    """Report whether the API process is healthy."""
    if settings.hosted_probe_mode:
        return Response(media_type="application/json")
    return health_status()


@app.get("/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}})
def readiness():
    """Bounded, cached dependency checks; never sends notifications or model requests."""
    if settings.hosted_probe_mode:
        return Response(status_code=200 if app.state.ready else 503, media_type="application/json")
    return readiness_status()


@app.get("/status/health", response_model=HealthResponse)
def health_status():
    return {"status": "ok", "service": "retail-inventory-agent"}


@app.get("/status/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}})
def readiness_status():
    result = get_container().readiness_service.check()
    return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503)
