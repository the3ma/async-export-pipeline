"""Liveness plus dependency checks (database, S3, SQS, DynamoDB)."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from export_api.deps import ServicesDep

log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(services: ServicesDep) -> JSONResponse:
    checks: dict[str, dict[str, object]] = {}
    for name, check in services.health_checks.items():
        started = time.perf_counter()
        try:
            check()
        except Exception as exc:
            # Log the cause, but don't echo connection strings or stack traces to callers.
            log.warning("health check failed", extra={"check": name, "error": repr(exc)})
            result: dict[str, object] = {"status": "error"}
        else:
            result = {"status": "ok"}
        result["ms"] = round((time.perf_counter() - started) * 1000, 1)
        checks[name] = result
    healthy = all(c["status"] == "ok" for c in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
