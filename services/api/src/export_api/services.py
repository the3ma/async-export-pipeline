"""The API's collaborators, bundled so tests can swap any of them for a fake."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from psycopg_pool import ConnectionPool

from exportkit import aws
from exportkit.jobstore import JobStore
from exportkit.queue import MessageSender, SqsSender

from .activity import ActivityRepository, PgActivityRepository
from .config import ApiSettings


class Presigner(Protocol):
    def presign_download(self, object_key: str, file_name: str, expires_in: int) -> str: ...


class S3Presigner:
    def __init__(self, s3_client: Any, bucket: str) -> None:
        self._s3 = s3_client
        self._bucket = bucket

    def presign_download(self, object_key: str, file_name: str, expires_in: int) -> str:
        return str(
            self._s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": object_key,
                    # File names are generated from enums + ids only, so quoting is safe.
                    "ResponseContentDisposition": f'attachment; filename="{file_name}"',
                },
                ExpiresIn=expires_in,
            )
        )


HealthCheck = Callable[[], None]


@dataclass
class Services:
    activity: ActivityRepository
    jobs: JobStore
    queue: MessageSender
    presigner: Presigner
    presign_seconds: int = 60
    health_checks: Mapping[str, HealthCheck] = field(default_factory=dict)
    close: Callable[[], None] = lambda: None


def build_services(settings: ApiSettings) -> Services:
    """Wire the real dependencies. The pool opens lazily so the API starts before the DB is up."""
    pool: ConnectionPool[Any] = ConnectionPool(
        settings.resolve_database_url(),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        open=False,
        kwargs={"autocommit": True, "connect_timeout": 5},
        timeout=10,
    )
    pool.open(wait=False)
    activity = PgActivityRepository(pool)
    s3 = aws.client("s3", settings)
    presign_s3 = aws.client("s3", settings, endpoint_url=settings.public_s3_endpoint_url)
    sqs = aws.client("sqs", settings)
    dynamodb = aws.client("dynamodb", settings)
    checks: dict[str, HealthCheck] = {
        "database": activity.ping,
        "s3": lambda: s3.head_bucket(Bucket=settings.export_bucket),
        "sqs": lambda: sqs.get_queue_attributes(
            QueueUrl=settings.export_queue_url, AttributeNames=["QueueArn"]
        ),
        "dynamodb": lambda: dynamodb.describe_table(TableName=settings.jobs_table),
    }
    return Services(
        activity=activity,
        jobs=JobStore(aws.dynamodb_table(settings)),
        queue=SqsSender(sqs, settings.export_queue_url),
        presigner=S3Presigner(presign_s3, settings.export_bucket),
        presign_seconds=settings.presign_seconds,
        health_checks=checks,
        close=pool.close,
    )
