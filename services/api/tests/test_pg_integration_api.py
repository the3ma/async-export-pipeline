"""PgActivityRepository against a real PostgreSQL 16. Skipped unless DATABASE_URL is set."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from export_api.activity import ActivityQuery, DownloadAudit, PgActivityRepository
from exportkit.ids import new_ulid
from exportkit.models import Action, Area

pytestmark = pytest.mark.integration


@pytest.fixture
def repository(app_url: str) -> Iterator[PgActivityRepository]:
    pool: ConnectionPool[Any] = ConnectionPool(
        app_url, min_size=1, max_size=2, kwargs={"autocommit": True}, open=True
    )
    try:
        yield PgActivityRepository(pool)
    finally:
        pool.close()


def test_grid_pages_are_keyset_ordered(repository: PgActivityRepository, pg_window: Any) -> None:
    repository.ping()
    seen: list[tuple[Any, int]] = []
    after = None
    while True:
        page = repository.list_page(
            ActivityQuery(from_=pg_window.start, to=pg_window.end, limit=10, after=after)
        )
        seen.extend((item.occurred_at, item.event_id) for item in page.items)
        if page.next_cursor is None:
            break
        last = page.items[-1]
        after = (last.occurred_at, last.event_id)
    assert seen == sorted(seen, reverse=True)
    assert sorted(event_id for _, event_id in seen) == sorted(pg_window.event_ids)


def test_grid_filters(repository: PgActivityRepository, pg_window: Any) -> None:
    page = repository.list_page(
        ActivityQuery(
            area=Area.BILLING,
            action=Action.UPDATE,
            from_=pg_window.start,
            to=pg_window.end,
            limit=100,
        )
    )
    assert page.items
    assert all(i.area is Area.BILLING and i.action is Action.UPDATE for i in page.items)


def test_download_audit_row_is_appended(repository: PgActivityRepository, app_url: str) -> None:
    export_id = new_ulid()
    repository.record_download(
        DownloadAudit(
            actor_id="usr_0001",
            area=Area.ORDERS,
            export_id=export_id,
            file_name="Activity_Orders_Summary_x.csv",
            export_area="orders",
        )
    )
    with psycopg.connect(app_url) as conn:
        row = conn.execute(
            "SELECT actor_id, area, action, entity_type, details FROM activity_event"
            " WHERE entity_id = %s",
            (export_id,),
        ).fetchone()
    assert row is not None
    assert row[:4] == ("usr_0001", "orders", "export", "download")
    assert row[4][0] == {"field": "file_name", "old": None, "new": "Activity_Orders_Summary_x.csv"}
