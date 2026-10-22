from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from exportkit.cursors import InvalidCursorError, encode_cursor
from exportkit.ids import new_ulid
from exportkit.jobstore import JobExistsError, JobStore, new_job
from exportkit.models import (
    ExportFormat,
    ExportMode,
    ExportRequest,
    FailureCode,
    JobStatus,
    KeysetCursor,
)

REQUEST = ExportRequest(area="billing", mode=ExportMode.SUMMARY, format=ExportFormat.CSV)


def make(store: JobStore, owner: str = "alice", export_id: str | None = None) -> str:
    job = store.create(new_job(owner, REQUEST, export_id or new_ulid()))
    return job.export_id


def test_create_and_get(store: JobStore) -> None:
    export_id = make(store)
    job = store.get(export_id)
    assert job is not None
    assert job.status is JobStatus.QUEUED
    assert job.file_name.endswith(f"_UTC_{export_id}.csv")
    assert job.object_key.endswith(f"/{export_id}/{job.file_name}")
    assert job.expires_at > 0
    assert store.get(new_ulid()) is None


def test_create_twice_is_rejected(store: JobStore) -> None:
    export_id = make(store)
    with pytest.raises(JobExistsError):
        make(store, export_id=export_id)


def test_owner_scoping(store: JobStore) -> None:
    export_id = make(store, owner="alice")
    assert store.get_for_owner(export_id, "alice") is not None
    assert store.get_for_owner(export_id, "mallory") is None


def test_happy_path_transitions(store: JobStore) -> None:
    export_id = make(store)
    claimed = store.claim(export_id, high_water_event_id=999)
    assert claimed is not None
    assert claimed.status is JobStatus.RUNNING
    assert claimed.high_water_event_id == 999
    assert claimed.started_at is not None
    assert store.claim(export_id, 1000) is None  # second claim loses

    planned = store.plan(
        export_id, expected_rows=25, part_count=3, file_name="x.zip", object_key="k/x.zip"
    )
    assert planned is not None and planned.parts_done == 0 and planned.part_count == 3

    cursor = KeysetCursor(occurred_at=datetime(2026, 1, 1, tzinfo=UTC), event_id=7)
    assert store.part_done(export_id, 2, cursor) is None  # out of order
    first = store.part_done(export_id, 1, cursor)
    assert first is not None and first.parts_done == 1 and first.next_cursor == cursor
    assert store.part_done(export_id, 1, cursor) is None  # exactly once

    done = store.complete(export_id, row_count=25)
    assert done is not None
    assert done.status is JobStatus.COMPLETED and done.row_count == 25
    assert done.completed_at is not None
    assert store.complete(export_id, row_count=25) is None
    assert store.fail(export_id, FailureCode.WORKER_ERROR) is None  # final stays final
    assert store.claim(export_id, 1) is None


def test_queued_can_fail_and_running_can_fail(store: JobStore) -> None:
    queued = make(store)
    failed = store.fail(queued, FailureCode.QUEUE_SEND_FAILED)
    assert failed is not None and failed.status is JobStatus.FAILED
    assert failed.failure_code is FailureCode.QUEUE_SEND_FAILED

    running = make(store)
    store.claim(running, 1)
    assert store.fail(running, FailureCode.TOO_LARGE) is not None
    assert store.complete(running, row_count=1) is None


def test_queued_job_cannot_complete_or_plan(store: JobStore) -> None:
    export_id = make(store)
    assert store.complete(export_id, row_count=1) is None
    assert (
        store.plan(export_id, expected_rows=1, part_count=1, file_name="a", object_key="b") is None
    )


def test_updates_never_create_items(store: JobStore) -> None:
    ghost = new_ulid()
    assert store.claim(ghost, 1) is None
    assert store.fail(ghost, FailureCode.WORKER_ERROR) is None
    assert store.get(ghost) is None


def test_response_only_codes_are_not_stored(store: JobStore) -> None:
    export_id = make(store)
    with pytest.raises(ValueError, match="response-only"):
        store.fail(export_id, FailureCode.EXPIRED)


def test_notified_once(store: JobStore) -> None:
    export_id = make(store)
    first = store.mark_notified(export_id)
    assert first is not None and first.notified_at is not None
    assert store.mark_notified(export_id) is None


def test_mark_seen_only_touches_own_jobs(store: JobStore) -> None:
    mine, theirs = make(store, "alice"), make(store, "bob")
    assert store.mark_seen("alice", [mine, theirs, new_ulid(), mine]) == [mine]
    job, other = store.get(mine), store.get(theirs)
    assert job is not None and job.seen_at is not None
    assert other is not None and other.seen_at is None


def test_list_newest_first_with_cursor(store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
    import exportkit.jobstore as jobstore_module

    clock = iter(datetime(2026, 5, 1, 12, minute, tzinfo=UTC) for minute in range(30))
    monkeypatch.setattr(jobstore_module, "utcnow", lambda: next(clock))
    created = [make(store, "alice") for _ in range(5)]
    make(store, "bob")

    page1, cursor = store.list_for_owner("alice", limit=2)
    page2, cursor2 = store.list_for_owner("alice", limit=2, cursor=cursor)
    page3, cursor3 = store.list_for_owner("alice", limit=2, cursor=cursor2)
    ids = [job.export_id for job in page1 + page2 + page3]
    assert ids == list(reversed(created))
    assert cursor and cursor2 and cursor3 is None
    assert all(job.owner_id == "alice" for job in page1 + page2 + page3)


def test_cursor_cannot_cross_owners(store: JobStore) -> None:
    make(store, "alice")
    make(store, "bob")
    _, cursor = store.list_for_owner("bob", limit=1)
    # A cursor minted for bob only ever constrains alice's own query: owner comes from the caller.
    jobs, _ = store.list_for_owner("alice", limit=10, cursor=cursor)
    assert all(job.owner_id == "alice" for job in jobs)
    with pytest.raises(InvalidCursorError):
        store.list_for_owner("alice", cursor=encode_cursor({"e": 1}))


def test_records_round_trip_filters(table: Any) -> None:
    store = JobStore(table)
    request = ExportRequest.model_validate(
        {
            "area": "all",
            "mode": "detailed",
            "format": "xlsx",
            "filters": {"from": "2026-01-01T00:00:00Z", "action": "delete"},
        }
    )
    job = store.create(new_job("alice", request, new_ulid()))
    loaded = store.get(job.export_id)
    assert loaded is not None
    assert loaded.filters == request.filters
    view = loaded.public_view()
    assert view["filters"] == {"from": "2026-01-01T00:00:00Z", "action": "delete"}
    assert "next_cursor" not in view and "expected_rows" not in view
