"""S3 object keys: ``exports/{owner_hash}/{export_id}/{file_name}``."""

from __future__ import annotations

import hashlib

EXPORT_PREFIX = "exports"


def owner_hash(owner_id: str) -> str:
    """First 16 hex chars of sha256(owner_id): groups an owner's objects without exposing the id."""
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:16]


def export_prefix(owner_id: str, export_id: str) -> str:
    return f"{EXPORT_PREFIX}/{owner_hash(owner_id)}/{export_id}/"


def object_key(owner_id: str, export_id: str, file_name: str) -> str:
    if "/" in file_name or "\\" in file_name:
        raise ValueError("file name must not contain path separators")
    return export_prefix(owner_id, export_id) + file_name
