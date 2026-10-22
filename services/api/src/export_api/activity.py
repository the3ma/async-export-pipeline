"""Activity log access: the grid page (keyset pagination) and the download audit row.

Routes depend on the :class:`ActivityRepository` protocol; production uses PostgreSQL through a
psycopg 3 connection pool, unit tests use an in-memory fake.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import AwareDatetime, BaseModel, ConfigDict

from exportkit.cursors import InvalidCursorError, decode_cursor, encode_cursor
from exportkit.models import Action, Area
from exportkit.timeutil import from_iso, to_iso


class ActivityItem(BaseModel):
    event_id: int
    occurred_at: datetime
    actor_id: str
    area: Area
    action: Action
    entity_type: str
    entity_id: str


class ActivityPage(BaseModel):
    items: list[ActivityItem]
    next_cursor: str | None


class ActivityQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    area: Area | None = None
    action: Action | None = None
    from_: AwareDatetime | None = None
    to: AwareDatetime | None = None
    limit: int = 100
    after: tuple[datetime, int] | None = None  # decoded keyset cursor


class DownloadAudit(BaseModel):
    actor_id: str
    area: Area
    export_id: str
    file_name: str
    export_area: str


def encode_activity_cursor(occurred_at: datetime, event_id: int) -> str:
    return encode_cursor({"t": to_iso(occurred_at), "id": event_id})


def decode_activity_cursor(token: str) -> tuple[datetime, int]:
    payload = decode_cursor(token)
    try:
        return from_iso(str(payload["t"])), int(payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCursorError("malformed cursor") from exc


class ActivityRepository(Protocol):
    def list_page(self, query: ActivityQuery) -> ActivityPage: ...

    def record_download(self, audit: DownloadAudit) -> None: ...

    def ping(self) -> None: ...


_COLUMNS = "event_id, occurred_at, actor_id, area, action, entity_type, entity_id"


class PgActivityRepository:
    def __init__(self, pool: ConnectionPool[Any]) -> None:
        self._pool = pool

    def list_page(self, query: ActivityQuery) -> ActivityPage:
        where = ["TRUE"]
        params: dict[str, Any] = {"limit": query.limit + 1}
        if query.area is not None:
            where.append("area = %(area)s")
            params["area"] = query.area.value
        if query.action is not None:
            where.append("action = %(action)s")
            params["action"] = query.action.value
        if query.from_ is not None:
            where.append("occurred_at >= %(from)s")
            params["from"] = query.from_
        if query.to is not None:
            where.append("occurred_at <= %(to)s")
            params["to"] = query.to
        if query.after is not None:
            where.append("(occurred_at, event_id) < (%(after_t)s, %(after_id)s)")
            params["after_t"], params["after_id"] = query.after
        sql = (
            f"SELECT {_COLUMNS} FROM activity_event WHERE {' AND '.join(where)} "
            "ORDER BY occurred_at DESC, event_id DESC LIMIT %(limit)s"
        )
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return _page(rows, query.limit)

    def record_download(self, audit: DownloadAudit) -> None:
        details = [
            {"field": "file_name", "old": None, "new": audit.file_name},
            {"field": "export_area", "old": None, "new": audit.export_area},
        ]
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO activity_event "
                "(occurred_at, actor_id, area, action, entity_type, entity_id, details) "
                "VALUES (now(), %s, %s, 'export', 'download', %s, %s)",
                (audit.actor_id, audit.area.value, audit.export_id, Jsonb(details)),
            )

    def ping(self) -> None:
        with self._pool.connection() as conn:
            conn.execute("SELECT 1").fetchone()


def _page(rows: list[tuple[Any, ...]], limit: int) -> ActivityPage:
    items = [ActivityItem(**dict(zip(_COLUMNS.split(", "), row, strict=True))) for row in rows]
    next_cursor = None
    if len(items) > limit:
        items = items[:limit]
        last = items[-1]
        next_cursor = encode_activity_cursor(last.occurred_at, last.event_id)
    return ActivityPage(items=items, next_cursor=next_cursor)
