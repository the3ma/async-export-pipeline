"""AWS Lambda entry point: ``export_worker.handler.handler``.

Wire it to the ``export-requests`` queue with batch size 1 and ``ReportBatchItemFailures``: a
record that raises is reported back, so only that message returns to the queue. When a message
fails on its last allowed receive (``MAX_RECEIVE_COUNT``, matching the redrive policy) the job is
marked ``failed`` / ``WORKER_ERROR`` before the message moves to the dead-letter queue.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from exportkit import aws
from exportkit.jobstore import JobStore
from exportkit.jsonlog import configure_logging
from exportkit.models import StageMessage, parse_queue_message
from exportkit.queue import SqsSender

from .config import WorkerSettings
from .notify import Notifier, SesNotifier, SmtpNotifier
from .rows import PgRowSource
from .stages import ExportWorker

log = logging.getLogger(__name__)


def build_notifier(settings: WorkerSettings) -> Notifier:
    domain = settings.mail_to_domain

    def recipient_for(owner_id: str) -> str:
        return f"{owner_id}@{domain}"  # demo mapping, see WorkerSettings.mail_to_domain

    if settings.mail_transport == "ses":
        return SesNotifier(
            ses_client=aws.client("sesv2", settings),
            sender=settings.mail_from,
            app_base_url=settings.app_base_url,
            recipient_for=recipient_for,
        )
    return SmtpNotifier(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.mail_from,
        app_base_url=settings.app_base_url,
        recipient_for=recipient_for,
        timeout=settings.smtp_timeout,
    )


def build_worker(settings: WorkerSettings) -> ExportWorker:
    return ExportWorker(
        jobs=JobStore(aws.dynamodb_table(settings)),
        rows=PgRowSource(settings.resolve_database_url(), fetch_size=settings.fetch_size),
        s3=aws.client("s3", settings),
        bucket=settings.export_bucket,
        queue=SqsSender(aws.client("sqs", settings), settings.export_queue_url),
        notifier=build_notifier(settings),
        part_rows=settings.part_rows,
        max_parts=settings.max_parts,
        s3_part_size=settings.s3_part_size,
        tmp_dir=settings.tmp_dir,
    )


def process_batch(
    worker: ExportWorker, event: dict[str, Any], *, max_receive_count: int
) -> dict[str, list[dict[str, str]]]:
    """Process an SQS event; return the partial-batch-failure response Lambda expects."""
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        message_id = str(record.get("messageId", ""))
        receives = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        message: StageMessage | None = None
        try:
            message = parse_queue_message(record["body"])
            outcome = worker.process(message)
            log.info(
                "message processed",
                extra={
                    "message_id": message_id,
                    "export_id": message.export_id,
                    "stage": message.stage,
                    "outcome": outcome.value,
                    "receive_count": receives,
                },
            )
        except ValidationError:
            # A malformed body can never succeed; let it reach the DLQ for inspection.
            log.exception("invalid queue message", extra={"message_id": message_id})
            failures.append({"itemIdentifier": message_id})
        except Exception:
            log.exception(
                "stage failed",
                extra={"message_id": message_id, "receive_count": receives},
            )
            if message is not None and receives >= max_receive_count:
                try:
                    worker.give_up(message.export_id)
                except Exception:
                    log.exception("could not mark job failed", extra={"message_id": message_id})
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}


_settings: WorkerSettings | None = None
_worker: ExportWorker | None = None


def handler(event: dict[str, Any], context: Any) -> dict[str, list[dict[str, str]]]:
    global _settings, _worker
    if _worker is None or _settings is None:  # cold start: reuse clients across invocations
        _settings = WorkerSettings()
        configure_logging("worker", _settings.log_level)
        _worker = build_worker(_settings)
    return process_batch(_worker, event, max_receive_count=_settings.max_receive_count)
