"""ULID generation (https://github.com/ulid/spec) without a third-party dependency.

A ULID is 48 bits of millisecond timestamp followed by 80 random bits, encoded as 26 Crockford
base32 characters. Lexicographic order equals creation order (to the millisecond), which keeps
object keys and log lines naturally sorted.
"""

from __future__ import annotations

import re
import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ULID_PATTERN = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def new_ulid(timestamp_ms: int | None = None) -> str:
    """Return a new ULID string. ``timestamp_ms`` is only for deterministic tests."""
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    if not 0 <= ts < 2**48:
        raise ValueError("timestamp out of ULID range")
    value = (ts << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        value, index = divmod(value, 32)
        chars.append(_ALPHABET[index])
    return "".join(reversed(chars))


def is_ulid(value: str) -> bool:
    return bool(ULID_PATTERN.fullmatch(value))
