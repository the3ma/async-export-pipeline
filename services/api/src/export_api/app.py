"""FastAPI application factory (see ``export_api.main`` for the served instance)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import ApiSettings
from .errors import ExportError, export_error_handler
from .routes import activity, exports, health
from .services import Services, build_services

log = logging.getLogger("export_api.access")


def create_app(services: Services | None = None, settings: ApiSettings | None = None) -> FastAPI:
    """Build the app. Pass ``services`` to run against fakes (tests); otherwise real AWS + DB."""
    settings = settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = services is None
        app.state.services = services or build_services(settings)
        try:
            yield
        finally:
            if owned:
                app.state.services.close()

    app = FastAPI(
        title="Activity export API",
        version="0.1.0",
        description="Browse the append-only activity log and export it asynchronously.",
        lifespan=lifespan,
    )
    app.state.auth_mode = settings.auth_mode
    if services is not None:
        app.state.services = services  # also usable without entering the lifespan
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-User-Id"],
        expose_headers=["Location"],
    )

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    app.add_exception_handler(ExportError, export_error_handler)
    app.include_router(exports.router)
    app.include_router(activity.router)
    app.include_router(health.router)
    return app
