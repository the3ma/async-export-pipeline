"""Shared pytest fixtures for all Python services: moto-backed S3, SQS and DynamoDB."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import boto3
import pytest
from moto import mock_aws

from exportkit.jobstore import OWNER_INDEX, JobStore
from exportkit.settings import AwsSettings

REGION = "us-east-1"
BUCKET = "exports"
TABLE = "exports"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="integration test: set DATABASE_URL to a PostgreSQL 16 database")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fake AWS for the duration of a test (never talks to LocalStack or a real account)."""
    for name in ("AWS_ENDPOINT_URL", "AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


@pytest.fixture
def aws_settings(aws: None, queue_url: str) -> AwsSettings:
    return AwsSettings(
        aws_region=REGION,
        aws_endpoint_url=None,
        export_bucket=BUCKET,
        export_queue_url=queue_url,
        jobs_table=TABLE,
    )


@pytest.fixture
def table(aws: None) -> Any:
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    return dynamodb.create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "export_id", "AttributeType": "S"},
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "requested_at", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "export_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": OWNER_INDEX,
                "KeySchema": [
                    {"AttributeName": "owner_id", "KeyType": "HASH"},
                    {"AttributeName": "requested_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )


@pytest.fixture
def store(table: Any) -> JobStore:
    return JobStore(table)


@pytest.fixture
def s3(aws: None) -> Any:
    client = boto3.client("s3", region_name=REGION)
    client.create_bucket(Bucket=BUCKET)
    return client


@pytest.fixture
def sqs(aws: None) -> Any:
    return boto3.client("sqs", region_name=REGION)


@pytest.fixture
def queue_url(sqs: Any) -> str:
    return str(sqs.create_queue(QueueName="export-requests")["QueueUrl"])


@pytest.fixture
def drain(sqs: Any, queue_url: str) -> Any:
    """Return a function that receives and deletes everything currently on the queue."""

    def _drain() -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while True:
            batch = sqs.receive_message(
                QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0
            ).get("Messages", [])
            if not batch:
                return messages
            for message in batch:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
            messages.extend(batch)

    return _drain


# -- PostgreSQL integration fixtures (only used by tests marked `integration`) ------------------

SCHEMA_SQL = Path(__file__).resolve().parents[1] / "db" / "001_schema.sql"


def role_url(url: str, user: str, password: str) -> str:
    """The same database, connected as another role (roles come from db/001_schema.sql)."""
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"{user}:{password}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Admin DATABASE_URL with the schema applied (idempotently)."""
    import psycopg

    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))  # type: ignore[arg-type]
    return url


@dataclass(frozen=True)
class PgWindow:
    """A private time window of seeded rows, so repeated runs never see each other's data."""

    start: datetime
    end: datetime
    event_ids: list[int]


@pytest.fixture
def pg_window(pg_url: str) -> PgWindow:
    import psycopg
    from psycopg.types.json import Jsonb

    start = datetime(2090, 1, 1, tzinfo=UTC) + timedelta(days=uuid.uuid4().int % 20_000)
    areas = ("accounts", "billing", "catalog", "orders", "settings")
    actions = ("create", "update", "delete", "view", "export", "sign_in")
    ids: list[int] = []
    with psycopg.connect(pg_url, autocommit=True) as conn:
        for i in range(47):
            row = conn.execute(
                "INSERT INTO activity_event (occurred_at, actor_id, area, action, entity_type,"
                " entity_id, details) VALUES (%s, %s, %s, %s, 'invoice', %s, %s)"
                " RETURNING event_id",
                (
                    start + timedelta(minutes=i // 3),  # shared timestamps: tie-break matters
                    f"usr_{i % 5:04d}",
                    areas[i % 5],
                    actions[i % 6],
                    f"invoice_{i:06d}",
                    Jsonb([{"field": "status", "old": "draft", "new": f"v{i}"}]),
                ),
            ).fetchone()
            assert row is not None
            ids.append(int(row[0]))
    return PgWindow(start=start, end=start + timedelta(days=1), event_ids=ids)


@pytest.fixture(scope="session")
def exporter_url(pg_url: str) -> str:
    return role_url(pg_url, "exporter", "exporter")


@pytest.fixture(scope="session")
def app_url(pg_url: str) -> str:
    return role_url(pg_url, "app", "app")
