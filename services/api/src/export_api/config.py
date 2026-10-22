"""API configuration from the environment."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from exportkit.settings import ServiceSettings


class ApiSettings(ServiceSettings):
    presign_seconds: int = Field(default=60, ge=1, le=3600)
    # Host the *browser* uses to reach object storage. Locally the API talks to
    # http://localstack:4566 but the browser needs http://localhost:4566; unset in AWS.
    public_s3_endpoint_url: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    # header: trust X-User-Id (set by nginx in the demo). jwt: the `sub` claim that the API
    # Gateway JWT authorizer verified, read from the Lambda request context.
    auth_mode: Literal["header", "jwt"] = "header"
    db_pool_min: int = Field(default=1, ge=0)
    db_pool_max: int = Field(default=10, ge=1)
