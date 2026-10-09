"""ASGI request and lifecycle logs shared by API and MCP containers."""

import logging
from time import perf_counter
from uuid import uuid4


class HttpLoggingMiddleware:
    def __init__(self, app, service: str):
        self.app = app
        self.service = service
        self.logger = logging.getLogger("app.http")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            async def lifecycle_send(message):
                if message["type"] in {"lifespan.startup.complete", "lifespan.shutdown.complete"}:
                    self.logger.info(message["type"], extra={"service": self.service})
                elif message["type"].endswith(".failed"):
                    self.logger.error(message["type"], extra={"service": self.service})
                await send(message)
            await self.app(scope, receive, lifecycle_send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        status = 500
        request_id = uuid4().hex

        async def response_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.encode("ascii"))
                ]
            await send(message)

        # Do not record headers, body, query string, or untrusted inbound IDs.
        context = {"service": self.service, "method": scope["method"],
                   "path": scope["path"], "request_id": request_id}
        try:
            await self.app(scope, receive, response_send)
        except Exception:
            self.logger.exception("request_failed", extra=context)
            raise
        finally:
            self.logger.info("request_completed", extra={
                **context, "status": status,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            })
