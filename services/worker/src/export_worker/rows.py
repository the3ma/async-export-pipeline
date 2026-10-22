"""Reading activity rows for an export.

``RowSource`` is the seam between the stage logic and PostgreSQL. ``PgRowSource`` streams through
a *named* (server-side) cursor, fetching ``itersize`` rows per round trip, so worker memory stays
flat no matter how many rows a part has. Every read is ordered ``occurred_at DESC, event_id DESC``,
continues strictly after a keyset cursor and never looks past the pinned high-water mark.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import psycopg

from exportkit.models import ALL_AREAS, ExportMode, JobRecord, KeysetCursor


@dataclass(frozen=True, slots=True)
class ActivityRow:
    event_id: int
    occurred_at: datetime
    actor_id: str
    area: str
    action: str
    entity_type: str
    entity_id: str
    details: Any = None  # only loaded for detailed exports

    @property
    def cursor(self) -> KeysetCursor:
        return KeysetCursor(occurred_at=self.occurred_at, event_id=self.event_id)


@dataclass(frozen=True, slots=True)
class Selection:
    """Which rows an export covers. ``area=None`` means all areas."""

    high_water: int
    area: str | None = None
    action: str | None = None
    from_: datetime | None = None
    to: datetime | None = None
    with_details: bool = False

    @classmethod
    def for_job(cls, job: JobRecord) -> Selection:
        if job.high_water_event_id is None:
            raise ValueError("job has no high-water mark yet")
        filters = job.filters
        return cls(
            high_water=job.high_water_event_id,
            area=None if job.area == ALL_AREAS else job.area,
            action=filters.action.value if filters.action else None,
            from_=filters.from_,
            to=filters.to,
            with_details=job.mode is ExportMode.DETAILED,
        )

    def matches(self, row: ActivityRow) -> bool:
        """Python twin of :func:`where_clause` (used by in-memory sources)."""
        return (
            row.event_id <= self.high_water
            and (self.area is None or row.area == self.area)
            and (self.action is None or row.action == self.action)
            and (self.from_ is None or row.occurred_at >= self.from_)
            and (self.to is None or row.occurred_at <= self.to)
        )


def where_clause(selection: Selection, after: KeysetCursor | None) -> tuple[str, dict[str, Any]]:
    clauses = ["event_id <= %(high_water)s"]
    params: dict[str, Any] = {"high_water": selection.high_water}
    if selection.area is not None:
        clauses.append("area = %(area)s")
        params["area"] = selection.area
    if selection.action is not None:
        clauses.append("action = %(action)s")
        params["action"] = selection.action
    if selection.from_ is not None:
        clauses.append("occurred_at >= %(from)s")
        params["from"] = selection.from_
    if selection.to is not None:
        clauses.append("occurred_at <= %(to)s")
        params["to"] = selection.to
    if after is not None:
        clauses.append("(occurred_at, event_id) < (%(after_at)s, %(after_id)s)")
        params["after_at"] = after.occurred_at
        params["after_id"] = after.event_id
    return " AND ".join(clauses), params


def after_cursor(row: ActivityRow, cursor: KeysetCursor | None) -> bool:
    """Python twin of the keyset predicate ``(occurred_at, event_id) < cursor``."""
    if cursor is None:
        return True
    return (row.occurred_at, row.event_id) < (cursor.occurred_at, cursor.event_id)


class RowSource(Protocol):
    def high_water(self) -> int:
        """Current ``MAX(event_id)`` (0 for an empty table)."""
        ...

    def count(self, selection: Selection) -> int: ...

    def stream(
        self, selection: Selection, *, after: KeysetCursor | None, limit: int | None
    ) -> Iterator[ActivityRow]:
        """Rows newest first, strictly after ``after``, at most ``limit`` of them."""
        ...


_BASE_COLUMNS = "event_id, occurred_at, actor_id, area, action, entity_type, entity_id"


class PgRowSource:
    def __init__(self, dsn: str, *, fetch_size: int = 5_000) -> None:
        self._dsn = dsn
        self._fetch_size = fetch_size

    def _connect(self) -> psycopg.Connection[Any]:
        conn = psycopg.connect(self._dsn, connect_timeout=10, application_name="export-worker")
        conn.read_only = True
        return conn

    def high_water(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(event_id), 0) FROM activity_event").fetchone()
        return int(row[0]) if row else 0

    def count(self, selection: Selection) -> int:
        where, params = where_clause(selection, None)
        with self._connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM activity_event WHERE {where}", params)
            result = row.fetchone()
        return int(result[0]) if result else 0

    def stream(
        self, selection: Selection, *, after: KeysetCursor | None, limit: int | None
    ) -> Iterator[ActivityRow]:
        where, params = where_clause(selection, after)
        columns = _BASE_COLUMNS + (", details" if selection.with_details else "")
        sql = (
            f"SELECT {columns} FROM activity_event WHERE {where} "
            "ORDER BY occurred_at DESC, event_id DESC"
        )
        if limit is not None:
            sql += " LIMIT %(limit)s"
            params["limit"] = limit
        with self._connect() as conn, conn.cursor(name="export_rows") as cursor:
            cursor.itersize = self._fetch_size
            cursor.execute(sql, params)
            for record in cursor:
                yield ActivityRow(*record)
