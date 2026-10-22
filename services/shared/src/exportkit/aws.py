"""boto3 client/resource factories that honour the shared settings."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config

from .settings import AwsSettings


def _config(settings: AwsSettings, **extra: Any) -> Config:
    return Config(
        region_name=settings.aws_region,
        connect_timeout=settings.aws_connect_timeout,
        read_timeout=settings.aws_read_timeout,
        retries={"mode": "standard", "max_attempts": 3},
        **extra,
    )


def client(service: str, settings: AwsSettings, *, endpoint_url: str | None = None) -> Any:
    """A boto3 client. ``endpoint_url`` overrides ``AWS_ENDPOINT_URL`` (used for presigning)."""
    endpoint = endpoint_url or settings.aws_endpoint_url
    extra: dict[str, Any] = {}
    if service == "s3":
        # Path-style keeps LocalStack (and URLs presigned against it) working without DNS tricks.
        extra["s3"] = {"addressing_style": "path" if endpoint else "auto"}
        extra["signature_version"] = "s3v4"
    return boto3.client(service, endpoint_url=endpoint, config=_config(settings, **extra))


def dynamodb_table(settings: AwsSettings) -> Any:
    resource = boto3.resource(
        "dynamodb", endpoint_url=settings.aws_endpoint_url, config=_config(settings)
    )
    return resource.Table(settings.jobs_table)
