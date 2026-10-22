"""Export-specific errors: FastAPI's ``{"detail": ...}`` plus a ``failure_code``."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from exportkit.models import FailureCode


class ExportError(Exception):
    def __init__(
        self, status_code: int, detail: str, failure_code: FailureCode, **extra: Any
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.failure_code = failure_code
        self.extra = extra


async def export_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ExportError)
    body = {"detail": exc.detail, "failure_code": exc.failure_code.value, **exc.extra}
    return JSONResponse(status_code=exc.status_code, content=body)
