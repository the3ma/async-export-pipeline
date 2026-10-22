"""Against a real PostgreSQL 16 (CI service container). Skipped unless DATABASE_URL is set."""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from worker_fakes import PART_ROWS, Harness

from export_worker.rows import PgRowSource, Selection
from exportkit.models import JobStatus

pytestmark = pytest.mark.integration


def window_selection(window: Any, high_water: int, **kwargs: Any) -> Selection:
    return Selection(high_water=high_water, from_=window.start, to=window.end, **kwargs)


def test_keyset_parts_cover_the_window_exactly_once(exporter_url: str, pg_window: Any) -> None:
    source = PgRowSource(exporter_url, fetch_size=100)
    high_water = source.high_water()
    selection = window_selection(pg_window, high_water)
    assert source.count(selection) == 47
    everything = [row.event_id for row in source.stream(selection, after=None, limit=None)]
    assert sorted(everything) == sorted(pg_window.event_ids)

    stitched: list[int] = []
    cursor = None
    while True:
        part = list(source.stream(selection, after=cursor, limit=10))
        if not part:
            break
        stitched.extend(row.event_id for row in part)
        cursor = part[-1].cursor
    assert stitched == everything  # same order, no gaps, no duplicates


def test_filters_details_and_high_water(exporter_url: str, pg_url: str, pg_window: Any) -> None:
    source = PgRowSource(exporter_url)
    high_water = source.high_water()
    billing = window_selection(pg_window, high_water, area="billing", with_details=True)
    rows = list(source.stream(billing, after=None, limit=None))
    assert rows and all(row.area == "billing" for row in rows)
    assert rows[0].details[0]["field"] == "status"
    summary = list(source.stream(window_selection(pg_window, high_water), after=None, limit=1))
    assert summary[0].details is None

    with psycopg.connect(pg_url, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO activity_event (occurred_at, actor_id, area, action, entity_type,"
            " entity_id) VALUES (%s, 'usr_9999', 'billing', 'view', 'invoice', 'late')",
            (pg_window.start,),
        )
    pinned = window_selection(pg_window, high_water)
    assert source.count(pinned) == 47  # the late row is above the pinned high-water mark
    assert source.count(window_selection(pg_window, source.high_water())) == 48


INSERT_ONE = (
    "INSERT INTO activity_event (occurred_at, actor_id, area, action, entity_type, entity_id)"
    " VALUES (now(), 'x', 'billing', 'view', 'x', 'x')"
)


def test_exporter_role_is_read_only(exporter_url: str, pg_window: Any) -> None:
    with psycopg.connect(exporter_url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM activity_event").fetchone()
        # Layer 1: the role's sessions default to read-only transactions.
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute(INSERT_ONE)
        # Layer 2: even with that switched off, the role has no INSERT privilege.
        conn.execute("SET default_transaction_read_only = off")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(INSERT_ONE)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE activity_event SET actor_id = 'tampered' WHERE event_id = %s",
        "DELETE FROM activity_event WHERE event_id = %s",
        "TRUNCATE activity_event",
    ],
)
def test_append_only_even_for_the_owner(pg_url: str, pg_window: Any, statement: str) -> None:
    params = (pg_window.event_ids[0],) if "%s" in statement else None
    with (
        psycopg.connect(pg_url, autocommit=True) as conn,
        pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"),
    ):
        conn.execute(statement, params)  # type: ignore[arg-type]


def test_end_to_end_multi_part_export_from_postgres(
    exporter_url: str, pg_window: Any, make_harness: Callable[..., Harness]
) -> None:
    h = make_harness(PgRowSource(exporter_url, fetch_size=7))
    export_id = h.submit(
        area="all",
        filters={"from": pg_window.start.isoformat(), "to": pg_window.end.isoformat()},
    )
    h.run()
    job = h.job(export_id)
    assert job.status is JobStatus.COMPLETED
    assert job.row_count == 47
    assert job.part_count == -(-47 // PART_ROWS)
    archive = zipfile.ZipFile(io.BytesIO(h.body(job.object_key)))
    ids = []
    for name in archive.namelist():
        rows = list(csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
        ids.extend(int(r[0]) for r in rows[1:])
    assert sorted(ids) == sorted(pg_window.event_ids)
