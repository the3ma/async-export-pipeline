"""Stage logic: ``export`` (plan) -> ``part N`` -> ``zip`` (CONTRACT "Worker stages").

Every stage first re-reads the job and decides from its *stored* state what is left to do, so an
at-least-once re-delivery is safe:

* ``export`` on a ``queued`` job claims it and pins the high-water mark atomically. On a
  ``running`` job it re-uses the pinned mark and plan (the previous attempt crashed midway).
* ``part N`` runs only when ``parts_done == N - 1``. If ``parts_done == N`` the part is done but the
  next message may have been lost, so the next message is sent again (harmless: it is idempotent
  too). If ``parts_done > N`` it is a no-op.
* ``zip`` on a ``completed`` job only finishes the clean-up (part deletion, notification).

Object writes are deterministic for a given job state (same rows, same key), so repeating one
after a crash overwrites the object with identical content.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from exportkit import naming
from exportkit.jobstore import JobStore
from exportkit.keys import object_key
from exportkit.models import (
    ExportFormat,
    ExportStageMessage,
    FailureCode,
    JobRecord,
    JobStatus,
    KeysetCursor,
    PartStageMessage,
    StageMessage,
    ZipStageMessage,
)
from exportkit.queue import MessageSender
from exportkit.timeutil import from_iso

from .notify import Notifier
from .rows import ActivityRow, RowSource, Selection
from .s3io import MIB, S3MultipartWriter, upload_file
from .writers import write_csv, write_xlsx
from .zipper import build_zip

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


class Outcome(StrEnum):
    COMPLETED = "completed"  # job finished in this invocation
    ADVANCED = "advanced"  # work done, next stage enqueued
    FAILED = "failed"  # job failed for a business reason (TOO_LARGE, ROWSET_CHANGED)
    NOOP = "noop"  # duplicate / already finished


class StageNotReadyError(RuntimeError):
    """The job is not in a state this message can act on yet; let the queue retry it."""


@dataclass
class _Counter:
    """Wraps a row iterator to remember how many rows went by and the last one."""

    rows: Iterator[ActivityRow]
    count: int = 0
    last: ActivityRow | None = None

    def __iter__(self) -> Iterator[ActivityRow]:
        for row in self.rows:
            self.count += 1
            self.last = row
            yield row


class ExportWorker:
    def __init__(
        self,
        *,
        jobs: JobStore,
        rows: RowSource,
        s3: Any,
        bucket: str,
        queue: MessageSender,
        notifier: Notifier,
        part_rows: int,
        max_parts: int,
        s3_part_size: int = 8 * MIB,
        tmp_dir: str | None = None,
    ) -> None:
        self._jobs = jobs
        self._rows = rows
        self._s3 = s3
        self._bucket = bucket
        self._queue = queue
        self._notifier = notifier
        self._part_rows = part_rows
        self._max_parts = max_parts
        self._s3_part_size = s3_part_size
        self._tmp_dir = tmp_dir

    # -- entry points --------------------------------------------------------------------------

    def process(self, message: StageMessage) -> Outcome:
        job = self._jobs.get(message.export_id)
        if job is None:
            log.warning("job not found; dropping message", extra={"export_id": message.export_id})
            return Outcome.NOOP
        if job.status.is_final and not isinstance(message, ZipStageMessage):
            return Outcome.NOOP
        match message:
            case ExportStageMessage():
                return self._export_stage(job)
            case PartStageMessage():
                return self._part_stage(job, message)
            case ZipStageMessage():
                return self._zip_stage(job)
        raise AssertionError(f"unknown stage {message!r}")  # pragma: no cover

    def give_up(self, export_id: str) -> None:
        """Retries are exhausted: mark the job ``failed`` / ``WORKER_ERROR`` and tell the owner."""
        failed = self._jobs.fail(export_id, FailureCode.WORKER_ERROR)
        if failed is not None:
            log.error("job failed after retries", extra={"export_id": export_id})
            self._notify(failed)

    # -- stages --------------------------------------------------------------------------------

    def _export_stage(self, job: JobRecord) -> Outcome:
        if job.status is JobStatus.QUEUED:
            claimed = self._jobs.claim(job.export_id, self._rows.high_water())
            if claimed is None:  # a concurrent duplicate won the claim
                return Outcome.NOOP
            job = claimed
        if job.expected_rows is None:
            planned = self._plan(job)
            if planned is None:
                return Outcome.FAILED if self._is_failed(job.export_id) else Outcome.NOOP
            job = planned
        if (job.part_count or 1) == 1:
            return self._single_file(job)
        if (job.parts_done or 0) == 0:
            self._queue.send(PartStageMessage(export_id=job.export_id, part=1, cursor=None))
            log.info("export split", extra={"export_id": job.export_id, "parts": job.part_count})
        return Outcome.ADVANCED

    def _plan(self, job: JobRecord) -> JobRecord | None:
        total = self._rows.count(Selection.for_job(job))
        log.info("export planned", extra={"export_id": job.export_id, "rows": total})
        if total > self._max_parts * self._part_rows:
            self._fail(job, FailureCode.TOO_LARGE)
            return None
        part_count = max(1, math.ceil(total / self._part_rows))
        requested = from_iso(job.requested_at)
        name = (
            naming.zip_name(job.area, job.mode, requested, job.export_id)
            if part_count > 1
            else job.file_name
        )
        return self._jobs.plan(
            job.export_id,
            expected_rows=total,
            part_count=part_count,
            file_name=name,
            object_key=object_key(job.owner_id, job.export_id, name),
        )

    def _single_file(self, job: JobRecord) -> Outcome:
        selection = Selection.for_job(job)
        counter = self._write(job, job.object_key, selection, after=None, limit=None)
        if counter.count != job.expected_rows:
            self._fail(job, FailureCode.ROWSET_CHANGED)
            return Outcome.FAILED
        completed = self._jobs.complete(job.export_id, row_count=counter.count)
        if completed is None:
            return Outcome.NOOP
        log.info("export completed", extra={"export_id": job.export_id, "rows": counter.count})
        self._notify(completed)
        return Outcome.COMPLETED

    def _part_stage(self, job: JobRecord, message: PartStageMessage) -> Outcome:
        if job.status is not JobStatus.RUNNING or job.part_count is None:
            raise StageNotReadyError(f"job {job.export_id} is not planned")
        part, part_count, done = message.part, job.part_count, job.parts_done or 0
        if part > part_count:
            log.warning("part beyond plan; dropping", extra={"export_id": job.export_id})
            return Outcome.NOOP
        if done >= part:
            if done == part:
                self._send_next(job, part)  # heal a chain whose next message was lost
            return Outcome.NOOP
        if done != part - 1:
            raise StageNotReadyError(f"part {part} arrived before part {done + 1} finished")

        # The stored cursor is authoritative; the message copy is informational.
        cursor = job.next_cursor if part > 1 else None
        if part > 1 and cursor is None:
            raise StageNotReadyError("missing resume cursor")
        if message.cursor is not None and message.cursor != cursor:
            log.warning("message cursor differs from job", extra={"export_id": job.export_id})
        key = self._part_key(job, part)
        counter = self._write(job, key, Selection.for_job(job), after=cursor, limit=self._part_rows)
        expected = self._expected_part_rows(job, part)
        if counter.count != expected or counter.last is None:
            log.error(
                "part row count mismatch",
                extra={"export_id": job.export_id, "part": part, "rows": counter.count},
            )
            self._fail(job, FailureCode.ROWSET_CHANGED)
            return Outcome.FAILED
        updated = self._jobs.part_done(job.export_id, part, counter.last.cursor)
        if updated is None:
            return Outcome.NOOP
        log.info("part written", extra={"export_id": job.export_id, "part": part})
        self._send_next(updated, part)
        return Outcome.ADVANCED

    def _zip_stage(self, job: JobRecord) -> Outcome:
        if job.status is JobStatus.FAILED:
            return Outcome.NOOP
        if job.status is JobStatus.COMPLETED:
            # Re-delivery after the job completed: finish whatever clean-up may be missing.
            self._delete_parts(job)
            self._notify(job)
            return Outcome.NOOP
        part_count = job.part_count or 0
        if job.status is not JobStatus.RUNNING or part_count < 2 or job.parts_done != part_count:
            raise StageNotReadyError(f"job {job.export_id} is not ready to zip")
        members = [
            (self._part_key(job, part), self._part_name(job, part))
            for part in range(1, part_count + 1)
        ]
        size = build_zip(
            self._s3,
            self._bucket,
            members,
            job.object_key,
            timestamp=from_iso(job.requested_at),
            part_size=self._s3_part_size,
        )
        # Completed is set before the parts are deleted: if the worker dies in between, a retry
        # sees `completed` and only deletes - the parts are never gone while the zip is missing.
        completed = self._jobs.complete(job.export_id, row_count=job.expected_rows or 0)
        if completed is None:
            return Outcome.NOOP
        self._delete_parts(completed)
        log.info("export zipped", extra={"export_id": job.export_id, "bytes": size})
        self._notify(completed)
        return Outcome.COMPLETED

    # -- helpers -------------------------------------------------------------------------------

    def _expected_part_rows(self, job: JobRecord, part: int) -> int:
        part_count, total = job.part_count or 1, job.expected_rows or 0
        if part < part_count:
            return self._part_rows
        return total - (part_count - 1) * self._part_rows

    def _send_next(self, job: JobRecord, finished_part: int) -> None:
        if finished_part < (job.part_count or 0):
            self._queue.send(
                PartStageMessage(
                    export_id=job.export_id, part=finished_part + 1, cursor=job.next_cursor
                )
            )
        else:
            self._queue.send(ZipStageMessage(export_id=job.export_id))

    def _part_name(self, job: JobRecord, part: int) -> str:
        return naming.part_name(
            job.area,
            job.mode,
            job.format,
            from_iso(job.requested_at),
            job.export_id,
            part,
            job.part_count or 1,
        )

    def _part_key(self, job: JobRecord, part: int) -> str:
        return object_key(job.owner_id, job.export_id, self._part_name(job, part))

    def _write(
        self,
        job: JobRecord,
        key: str,
        selection: Selection,
        *,
        after: KeysetCursor | None,
        limit: int | None,
    ) -> _Counter:
        counter = _Counter(self._rows.stream(selection, after=after, limit=limit))
        detailed = selection.with_details
        if job.format is ExportFormat.CSV:
            with S3MultipartWriter(
                self._s3,
                self._bucket,
                key,
                part_size=self._s3_part_size,
                content_type=CONTENT_TYPES["csv"],
            ) as out:
                write_csv(counter, out, detailed=detailed)
            return counter
        with tempfile.TemporaryDirectory(dir=self._tmp_dir) as scratch:
            path = os.path.join(scratch, "export.xlsx")
            write_xlsx(counter, path, detailed=detailed, tmp_dir=scratch)
            upload_file(
                self._s3,
                self._bucket,
                key,
                path,
                part_size=self._s3_part_size,
                content_type=CONTENT_TYPES["xlsx"],
            )
        return counter

    def _delete_parts(self, job: JobRecord) -> None:
        part_count = job.part_count or 0
        if part_count < 2:
            return
        keys = [{"Key": self._part_key(job, part)} for part in range(1, part_count + 1)]
        self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})

    def _fail(self, job: JobRecord, code: FailureCode) -> None:
        failed = self._jobs.fail(job.export_id, code)
        if failed is not None:
            log.warning("export failed", extra={"export_id": job.export_id, "code": code.value})
            self._notify(failed)

    def _is_failed(self, export_id: str) -> bool:
        current = self._jobs.get(export_id)
        return current is not None and current.status is JobStatus.FAILED

    def _notify(self, job: JobRecord) -> None:
        """Best effort: the UI tray is the primary channel, so a mail outage never fails a job."""
        if job.notified_at is not None:
            return
        try:
            self._notifier.send(job)
        except Exception:
            log.exception("notification failed", extra={"export_id": job.export_id})
            return
        self._jobs.mark_notified(job.export_id)
