"""Local runner: long-polls SQS and feeds each message to the same code path as the Lambda.

``python -m export_worker.poller`` (or the ``export-worker-poller`` script). Messages the handler
reports as failed are left on the queue; their visibility timeout expires, SQS re-delivers them,
and after ``maxReceiveCount`` the redrive policy moves them to the DLQ - exactly like Lambda.
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType
from typing import Any

from exportkit import aws
from exportkit.jsonlog import configure_logging

from .config import WorkerSettings
from .handler import build_worker, process_batch
from .stages import ExportWorker

log = logging.getLogger(__name__)


def to_lambda_event(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape ``ReceiveMessage`` output like a Lambda SQS event."""
    return {
        "Records": [
            {
                "messageId": m["MessageId"],
                "receiptHandle": m["ReceiptHandle"],
                "body": m["Body"],
                "attributes": m.get("Attributes", {}),
                "eventSource": "aws:sqs",
            }
            for m in messages
        ]
    }


def poll_once(sqs: Any, queue_url: str, worker: ExportWorker, settings: WorkerSettings) -> int:
    response = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,  # batch size 1, as in AWS
        WaitTimeSeconds=settings.poll_wait_seconds,
        MessageSystemAttributeNames=["ApproximateReceiveCount"],
    )
    messages = response.get("Messages", [])
    if not messages:
        return 0
    event = to_lambda_event(messages)
    result = process_batch(worker, event, max_receive_count=settings.max_receive_count)
    failed = {f["itemIdentifier"] for f in result["batchItemFailures"]}
    for record in event["Records"]:
        if record["messageId"] not in failed:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=record["receiptHandle"])
        elif settings.retry_delay_seconds is not None:
            # Locally, retry sooner than the (Lambda-sized) queue visibility timeout.
            sqs.change_message_visibility(
                QueueUrl=queue_url,
                ReceiptHandle=record["receiptHandle"],
                VisibilityTimeout=settings.retry_delay_seconds,
            )
    return len(messages)


def sqs_client(settings: WorkerSettings) -> Any:
    # The read timeout must outlast the long poll. If the client gives up first, the queue still
    # completes the abandoned request: a message arriving then is delivered to nobody and stays
    # invisible for the whole visibility timeout.
    return aws.client(
        "sqs", settings, read_timeout=settings.poll_wait_seconds + settings.aws_read_timeout
    )


def main() -> None:
    settings = WorkerSettings()
    configure_logging("worker-poller", settings.log_level)
    worker = build_worker(settings)
    sqs = sqs_client(settings)
    stopping = False

    def stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        log.info("shutting down after the current message", extra={"signal": signum})
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("polling", extra={"queue_url": settings.export_queue_url})
    while not stopping:
        try:
            poll_once(sqs, settings.export_queue_url, worker, settings)
        except Exception:
            log.exception("poll failed; backing off")
            time.sleep(5)


if __name__ == "__main__":
    main()
