"""Opaque pagination cursors: base64url-encoded JSON, passed back verbatim as ``?cursor=``."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any


class InvalidCursorError(ValueError):
    """The client sent a cursor we did not issue (or tampered with it)."""


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(token: str) -> dict[str, Any]:
    try:
        padded = token + "=" * (-len(token) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise InvalidCursorError("malformed cursor") from exc
    if not isinstance(value, dict):
        raise InvalidCursorError("malformed cursor")
    return value
