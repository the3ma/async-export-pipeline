"""Export job records in DynamoDB.

Every state change is a *conditional* update, so concurrent or re-delivered workers can never
move a job backwards (``completed`` -> ``running``) or finish it twice. Legal transitions::

    queued -> running -> completed
    queued -> failed
    running -> failed

Methods that perform a transition return the updated record, or ``None`` when the condition did
not hold (the caller decides whether that is a benign duplicate or an error).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from botocore.exceptions import ClientError

from . import keys, naming
from .cursors import InvalidCursorError, decode_cursor, encode_cursor
from .models import ExportRequest, FailureCode, JobRecord, JobStatus, KeysetCursor
from .timeutil import expiry_epoch, to_iso, utcnow

OWNER_INDEX = "gsi_owner_requested"
_CONDITION_FAILED = "ConditionalCheckFailedException"


class JobExistsError(RuntimeError):
    """A job with this id already exists."""


def new_job(owner_id: str, request: ExportRequest, export_id: str) -> JobRecord:
    """A fresh ``queued`` job. The file name assumes a single file; multi-part plans rename it."""
    now = utcnow()
    name = naming.file_name(request.area, request.mode, request.format, now, export_id)
    return JobRecord(
        export_id=export_id,
        owner_id=owner_id,
        area=request.area,
        mode=request.mode,
        format=request.format,
        filters=request.filters,
        status=JobStatus.QUEUED,
        requested_at=to_iso(now),
        file_name=name,
        object_key=keys.object_key(owner_id, export_id, name),
        expires_at=expiry_epoch(now),
    )


class JobStore:
    def __init__(self, table: Any) -> None:
        self._table = table

    # -- reads -------------------------------------------------------------------------------

    def get(self, export_id: str) -> JobRecord | None:
        item = self._table.get_item(Key={"export_id": export_id}, ConsistentRead=True).get("Item")
        return JobRecord.model_validate(item) if item else None

    def get_for_owner(self, export_id: str, owner_id: str) -> JobRecord | None:
        """The job, or ``None`` if it does not exist *or* belongs to someone else."""
        job = self.get(export_id)
        return job if job is not None and job.owner_id == owner_id else None

    def list_for_owner(
        self, owner_id: str, *, limit: int = 20, cursor: str | None = None
    ) -> tuple[list[JobRecord], str | None]:
        """Newest first through the owner GSI. ``cursor`` is the opaque token returned before."""
        kwargs: dict[str, Any] = {
            "IndexName": OWNER_INDEX,
            "KeyConditionExpression": "owner_id = :owner",
            "ExpressionAttributeValues": {":owner": owner_id},
            "ScanIndexForward": False,
            "Limit": limit,
        }
        if cursor:
            kwargs["ExclusiveStartKey"] = self._start_key(cursor, owner_id)
        response = self._table.query(**kwargs)
        jobs = [JobRecord.model_validate(item) for item in response.get("Items", [])]
        last = response.get("LastEvaluatedKey")
        next_cursor = None
        if last and len(jobs) == limit:
            next_cursor = encode_cursor(
                {"e": str(last["export_id"]), "r": str(last["requested_at"])}
            )
        return jobs, next_cursor

    @staticmethod
    def _start_key(cursor: str, owner_id: str) -> dict[str, str]:
        payload = decode_cursor(cursor)
        export_id, requested_at = payload.get("e"), payload.get("r")
        if not isinstance(export_id, str) or not isinstance(requested_at, str):
            raise InvalidCursorError("malformed cursor")
        # The owner is never taken from the cursor, so a forged cursor cannot cross owners.
        return {"export_id": export_id, "requested_at": requested_at, "owner_id": owner_id}

    # -- writes ------------------------------------------------------------------------------

    def create(self, job: JobRecord) -> JobRecord:
        if job.status is not JobStatus.QUEUED:
            raise ValueError("new jobs start as queued")
        try:
            self._table.put_item(
                Item=job.to_item(), ConditionExpression="attribute_not_exists(export_id)"
            )
        except ClientError as exc:
            if _is_condition_failure(exc):
                raise JobExistsError(job.export_id) from exc
            raise
        return job

    def claim(self, export_id: str, high_water_event_id: int) -> JobRecord | None:
        """``queued`` -> ``running`` and pin the high-water mark in the same atomic write."""
        return self._update(
            export_id,
            {
                "#status": JobStatus.RUNNING.value,
                "started_at": to_iso(utcnow()),
                "high_water_event_id": high_water_event_id,
            },
            condition="#status = :expected",
            values={":expected": JobStatus.QUEUED.value},
        )

    def plan(
        self,
        export_id: str,
        *,
        expected_rows: int,
        part_count: int,
        file_name: str,
        object_key: str,
    ) -> JobRecord | None:
        """Record the row count pinned at the start and how the export is split."""
        return self._update(
            export_id,
            {
                "expected_rows": expected_rows,
                "part_count": part_count,
                "parts_done": 0,
                "file_name": file_name,
                "object_key": object_key,
            },
            condition="#status = :running",
            values={":running": JobStatus.RUNNING.value},
        )

    def part_done(
        self, export_id: str, part: int, next_cursor: KeysetCursor | None
    ) -> JobRecord | None:
        """Advance ``parts_done`` from ``part - 1`` to ``part`` (exactly once per part)."""
        fields: dict[str, Any] = {"parts_done": part}
        if next_cursor is not None:
            fields["next_cursor"] = next_cursor.model_dump(mode="json")
        return self._update(
            export_id,
            fields,
            condition="#status = :running AND parts_done = :previous",
            values={":running": JobStatus.RUNNING.value, ":previous": part - 1},
        )

    def complete(self, export_id: str, *, row_count: int) -> JobRecord | None:
        now = utcnow()
        return self._update(
            export_id,
            {
                "#status": JobStatus.COMPLETED.value,
                "completed_at": to_iso(now),
                "row_count": row_count,
                "expires_at": expiry_epoch(now),
            },
            condition="#status = :running",
            values={":running": JobStatus.RUNNING.value},
        )

    def fail(self, export_id: str, code: FailureCode) -> JobRecord | None:
        """Move a non-final job to ``failed``. Returns ``None`` if it had already finished."""
        if code in (FailureCode.EXPIRED, FailureCode.NOT_READY):
            raise ValueError(f"{code} is a response-only code")
        return self._update(
            export_id,
            {"#status": JobStatus.FAILED.value, "failure_code": code.value},
            condition="#status IN (:queued, :running)",
            values={":queued": JobStatus.QUEUED.value, ":running": JobStatus.RUNNING.value},
        )

    def mark_notified(self, export_id: str) -> JobRecord | None:
        return self._update(
            export_id,
            {"notified_at": to_iso(utcnow())},
            condition="attribute_not_exists(notified_at)",
            values={},
        )

    def mark_seen(self, owner_id: str, export_ids: Iterable[str]) -> list[str]:
        """Set ``seen_at`` on the caller's own jobs; ids of other owners are silently skipped."""
        seen_at = to_iso(utcnow())
        updated = []
        for export_id in dict.fromkeys(export_ids):
            job = self._update(
                export_id,
                {"seen_at": seen_at},
                condition="owner_id = :owner",
                values={":owner": owner_id},
            )
            if job is not None:
                updated.append(export_id)
        return updated

    # -- internals ---------------------------------------------------------------------------

    def _update(
        self,
        export_id: str,
        fields: Mapping[str, Any],
        *,
        condition: str,
        values: Mapping[str, Any],
    ) -> JobRecord | None:
        names = {"#status": "status"} if "#status" in condition or "#status" in fields else {}
        assignments = []
        expression_values = dict(values)
        for index, (name, value) in enumerate(fields.items()):
            placeholder = f":v{index}"
            assignments.append(f"{name} = {placeholder}")
            expression_values[placeholder] = value
        kwargs: dict[str, Any] = {
            "Key": {"export_id": export_id},
            "UpdateExpression": "SET " + ", ".join(assignments),
            # Never create an item through an update.
            "ConditionExpression": f"attribute_exists(export_id) AND ({condition})",
            "ExpressionAttributeValues": expression_values,
            "ReturnValues": "ALL_NEW",
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        try:
            response = self._table.update_item(**kwargs)
        except ClientError as exc:
            if _is_condition_failure(exc):
                return None
            raise
        return JobRecord.model_validate(response["Attributes"])


def _is_condition_failure(exc: ClientError) -> bool:
    return bool(exc.response.get("Error", {}).get("Code") == _CONDITION_FAILED)
