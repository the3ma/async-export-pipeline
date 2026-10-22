"""Environment configuration shared by the API and the worker (CONTRACT "Runtime configuration")."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Required configuration is missing or malformed."""


class AwsSettings(BaseSettings):
    """AWS resources. ``AWS_ENDPOINT_URL`` points at LocalStack locally and is unset in AWS."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    export_bucket: str = "exports"
    export_queue_url: str = "http://localhost:4566/000000000000/export-requests"
    jobs_table: str = "exports"
    aws_connect_timeout: float = Field(default=3.0, gt=0)
    aws_read_timeout: float = Field(default=10.0, gt=0)
    log_level: str = "INFO"


class ServiceSettings(AwsSettings):
    """AWS settings plus the database location.

    Locally ``DATABASE_URL`` is set directly. In AWS it is unset and ``DATABASE_SECRET_ARN`` names
    a Secrets Manager secret instead, so passwords never sit in the Lambda environment.
    """

    database_url: str | None = None
    database_secret_arn: str | None = None

    def resolve_database_url(self, secrets_client: Any | None = None) -> str:
        if self.database_url:
            return self.database_url
        if not self.database_secret_arn:
            raise ConfigurationError("set DATABASE_URL or DATABASE_SECRET_ARN")
        if secrets_client is None:
            from . import aws  # local import: aws imports this module

            secrets_client = aws.client("secretsmanager", self)
        response = secrets_client.get_secret_value(SecretId=self.database_secret_arn)
        try:
            secret = json.loads(response["SecretString"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError("database secret is not a JSON SecretString") from exc
        return database_url_from_secret(secret)


def database_url_from_secret(secret: dict[str, Any]) -> str:
    """Build a libpq URL from an RDS-style secret (``url`` wins when present)."""
    if secret.get("url"):
        return str(secret["url"])
    try:
        user = quote(str(secret["username"]), safe="")
        password = quote(str(secret["password"]), safe="")
        host = str(secret["host"])
        port = int(secret.get("port", 5432))
        dbname = quote(str(secret.get("dbname", "activity")), safe="")
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError("database secret lacks username/password/host") from exc
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
