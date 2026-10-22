"""UTC time helpers. Stored timestamps are ISO-8601 UTC strings ending in ``Z``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

RETENTION = timedelta(days=7)


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    """Fixed-width ISO-8601 (millisecond precision) so strings sort chronologically."""
    if value.tzinfo is None:
        raise ValueError("naive datetimes are not allowed")
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry a UTC offset")
    return parsed.astimezone(UTC)


def expiry_epoch(start: datetime) -> int:
    """Epoch seconds ``RETENTION`` after ``start`` (the DynamoDB TTL attribute)."""
    return int((start + RETENTION).timestamp())
