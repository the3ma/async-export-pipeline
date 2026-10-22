"""In-memory stand-ins for the API's database layer and presigner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from export_api.activity import (
    ActivityItem,
    ActivityPage,
    ActivityQuery,
    DownloadAudit,
    encode_activity_cursor,
)
from exportkit.models import Action, Area


@dataclass
class FakeActivityRepository:
    items: list[ActivityItem] = field(default_factory=list)
    audits: list[DownloadAudit] = field(default_factory=list)
    queries: list[ActivityQuery] = field(default_factory=list)
    fail_audit: bool = False
    fail_ping: bool = False

    def list_page(self, query: ActivityQuery) -> ActivityPage:
        self.queries.append(query)
        rows = sorted(self.items, key=lambda r: (r.occurred_at, r.event_id), reverse=True)
        rows = [
            r
            for r in rows
            if (query.area is None or r.area == query.area)
            and (query.action is None or r.action == query.action)
            and (query.from_ is None or r.occurred_at >= query.from_)
            and (query.to is None or r.occurred_at <= query.to)
            and (query.after is None or (r.occurred_at, r.event_id) < query.after)
        ]
        page = rows[: query.limit]
        more = len(rows) > query.limit
        cursor = encode_activity_cursor(page[-1].occurred_at, page[-1].event_id) if more else None
        return ActivityPage(items=page, next_cursor=cursor)

    def record_download(self, audit: DownloadAudit) -> None:
        if self.fail_audit:
            raise RuntimeError("database unavailable")
        self.audits.append(audit)

    def ping(self) -> None:
        if self.fail_ping:
            raise RuntimeError("database unavailable")


def sample_items(count: int) -> list[ActivityItem]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    areas, actions = list(Area), list(Action)
    return [
        ActivityItem(
            event_id=i,
            # Pairs of rows share a timestamp so the event_id tie-breaker matters.
            occurred_at=start + timedelta(minutes=i // 2),
            actor_id=f"usr_{i % 7:04d}",
            area=areas[i % len(areas)],
            action=actions[i % len(actions)],
            entity_type="invoice",
            entity_id=f"invoice_{i:06d}",
        )
        for i in range(1, count + 1)
    ]


@dataclass
class RecordingPresigner:
    """Captures calls and how many audit rows existed at presign time (ordering proof)."""

    repository: FakeActivityRepository
    calls: list[tuple[str, str, int]] = field(default_factory=list)
    audits_at_call: list[int] = field(default_factory=list)

    def presign_download(self, object_key: str, file_name: str, expires_in: int) -> str:
        self.calls.append((object_key, file_name, expires_in))
        self.audits_at_call.append(len(self.repository.audits))
        return f"https://storage.example.test/{object_key}?X-Amz-Expires={expires_in}"


class FailingSender:
    def send(self, message: object) -> str:
        raise ConnectionError("queue unavailable")
