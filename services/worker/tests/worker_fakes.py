"""In-memory row source and notifier for exercising the worker stages without PostgreSQL."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from export_worker.handler import process_batch
from export_worker.rows import ActivityRow, Selection, after_cursor
from export_worker.stages import ExportWorker
from exportkit.ids import new_ulid
from exportkit.jobstore import JobStore, new_job
from exportkit.models import ExportRequest, JobRecord, KeysetCursor

AREAS = ("accounts", "billing", "catalog", "orders", "settings")
ACTIONS = ("create", "update", "delete", "view", "export", "sign_in")
START = datetime(2026, 1, 1, tzinfo=UTC)


def make_row(event_id: int, *, area: str | None = None, minutes: int | None = None) -> ActivityRow:
    return ActivityRow(
        event_id=event_id,
        # Three rows share each timestamp, so continuing a part relies on the event_id tie-break.
        occurred_at=START + timedelta(minutes=event_id // 3 if minutes is None else minutes),
        actor_id=f"usr_{event_id % 11:04d}",
        area=area or AREAS[event_id % len(AREAS)],
        action=ACTIONS[event_id % len(ACTIONS)],
        entity_type="invoice",
        entity_id=f"invoice_{event_id:06d}",
        details=[{"field": "status", "old": "draft", "new": "sent"}],
    )


@dataclass
class FakeRowSource:
    """Same semantics as ``PgRowSource``: filters, high-water, keyset order and LIMIT."""

    rows: list[ActivityRow] = field(default_factory=list)
    streams: list[tuple[KeysetCursor | None, int | None]] = field(default_factory=list)

    @classmethod
    def with_rows(cls, count: int, **kwargs: str) -> FakeRowSource:
        return cls([make_row(i, **kwargs) for i in range(1, count + 1)])

    def high_water(self) -> int:
        return max((row.event_id for row in self.rows), default=0)

    def count(self, selection: Selection) -> int:
        return sum(1 for row in self.rows if selection.matches(row))

    def stream(
        self, selection: Selection, *, after: KeysetCursor | None, limit: int | None
    ) -> Iterator[ActivityRow]:
        self.streams.append((after, limit))
        ordered = sorted(self.rows, key=lambda r: (r.occurred_at, r.event_id), reverse=True)
        produced = 0
        for row in ordered:
            if limit is not None and produced >= limit:
                return
            if selection.matches(row) and after_cursor(row, after):
                produced += 1
                yield row if selection.with_details else _without_details(row)


def _without_details(row: ActivityRow) -> ActivityRow:
    return ActivityRow(
        row.event_id,
        row.occurred_at,
        row.actor_id,
        row.area,
        row.action,
        row.entity_type,
        row.entity_id,
        None,
    )


@dataclass
class RecordingNotifier:
    sent: list[JobRecord] = field(default_factory=list)
    fail: bool = False

    def send(self, job: JobRecord) -> None:
        if self.fail:
            raise ConnectionError("smtp down")
        self.sent.append(job)


BUCKET = "exports"
PART_ROWS = 10
MAX_PARTS = 5


@dataclass
class Harness:
    worker: ExportWorker
    rows: FakeRowSource
    notifier: RecordingNotifier
    store: JobStore
    s3: Any
    sqs: Any
    queue_url: str
    drain: Callable[[], list[dict[str, Any]]]

    def submit(self, owner: str = "alice", **request: Any) -> str:
        body = {"area": "billing", "mode": "summary", "format": "csv", **request}
        job = self.store.create(new_job(owner, ExportRequest.model_validate(body), new_ulid()))
        self.sqs.send_message(
            QueueUrl=self.queue_url,
            MessageBody=f'{{"export_id":"{job.export_id}","stage":"export"}}',
        )
        return job.export_id

    def run(self, *, duplicate: bool = False, steps: int | None = None) -> list[str]:
        """Deliver queued messages (all, or ``steps`` rounds); return the bodies processed."""
        bodies: list[str] = []
        for _ in range(steps or 200):
            messages = self.drain()
            if not messages:
                return bodies
            for message in messages:
                record = {
                    "messageId": message["MessageId"],
                    "body": message["Body"],
                    "attributes": {"ApproximateReceiveCount": "1"},
                }
                deliveries = 2 if duplicate else 1
                for _ in range(deliveries):
                    result = process_batch(self.worker, {"Records": [record]}, max_receive_count=3)
                    assert result == {"batchItemFailures": []}, message["Body"]
                bodies.append(message["Body"])
        if steps is None:
            raise AssertionError("queue never drained")
        return bodies

    def job(self, export_id: str) -> JobRecord:
        job = self.store.get(export_id)
        assert job is not None
        return job

    def keys(self) -> list[str]:
        listing = self.s3.list_objects_v2(Bucket=BUCKET)
        return sorted(obj["Key"] for obj in listing.get("Contents", []))

    def body(self, key: str) -> bytes:
        return bytes(self.s3.get_object(Bucket=BUCKET, Key=key)["Body"].read())
