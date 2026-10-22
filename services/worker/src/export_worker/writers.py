"""CSV and XLSX encoders that consume a row iterator once and never hold the whole file.

* CSV goes through the stdlib ``csv`` module into a small text buffer that is flushed to the
  binary sink every ``flush_bytes``, so it can stream straight into an S3 multipart upload.
* XLSX uses xlsxwriter in ``constant_memory`` mode: each row is flushed to a temp file as soon
  as the next row starts, and the workbook (itself a zip) is assembled on local disk at close.
  The finished file is then uploaded in multipart chunks.

Summary exports have the seven base columns; detailed exports add ``details`` (JSON text).
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from datetime import UTC
from typing import Any, BinaryIO, Protocol

import xlsxwriter

from .rows import ActivityRow

SUMMARY_COLUMNS = (
    "event_id",
    "occurred_at",
    "actor_id",
    "area",
    "action",
    "entity_type",
    "entity_id",
)
DETAILED_COLUMNS = (*SUMMARY_COLUMNS, "details")
XLSX_MAX_DATA_ROWS = 1_048_575  # sheet limit (1,048,576) minus the header row

# A cell starting with one of these may be evaluated as a formula by spreadsheet apps.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class BinarySink(Protocol):
    def write(self, data: bytes, /) -> int: ...


def columns(detailed: bool) -> tuple[str, ...]:
    return DETAILED_COLUMNS if detailed else SUMMARY_COLUMNS


def _safe_text(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_PREFIXES) else value


def _details_text(details: Any) -> str:
    return json.dumps(details if details is not None else [], separators=(",", ":"))


def _csv_values(row: ActivityRow, detailed: bool) -> list[str | int]:
    values: list[str | int] = [
        row.event_id,
        row.occurred_at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        _safe_text(row.actor_id),
        row.area,
        row.action,
        _safe_text(row.entity_type),
        _safe_text(row.entity_id),
    ]
    if detailed:
        values.append(_details_text(row.details))
    return values


def write_csv(
    rows: Iterable[ActivityRow],
    sink: BinarySink | BinaryIO,
    *,
    detailed: bool,
    flush_bytes: int = 1024 * 1024,
) -> None:
    """Encode ``rows`` as UTF-8 CSV (with BOM so spreadsheet apps detect the encoding)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    buffer.write("\ufeff")
    writer.writerow(columns(detailed))
    for row in rows:
        writer.writerow(_csv_values(row, detailed))
        if buffer.tell() >= flush_bytes:
            sink.write(buffer.getvalue().encode("utf-8"))
            buffer.seek(0)
            buffer.truncate()
    if buffer.tell():
        sink.write(buffer.getvalue().encode("utf-8"))


def write_xlsx(
    rows: Iterable[ActivityRow], path: str, *, detailed: bool, tmp_dir: str | None = None
) -> None:
    """Write an XLSX workbook to ``path`` with bounded memory (``constant_memory`` mode)."""
    options: dict[str, Any] = {"constant_memory": True, "remove_timezone": True}
    if tmp_dir:
        options["tmpdir"] = tmp_dir
    workbook = xlsxwriter.Workbook(path, options)
    try:
        sheet = workbook.add_worksheet("activity")
        header = workbook.add_format({"bold": True})
        timestamp = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss.000"})
        names = columns(detailed)
        for col, name in enumerate(names):
            sheet.write_string(0, col, name, header)
        sheet.set_column(1, 1, 24)
        sheet.freeze_panes(1, 0)
        for index, row in enumerate(rows, start=1):
            if index > XLSX_MAX_DATA_ROWS:
                raise ValueError("too many rows for one XLSX sheet")
            sheet.write_number(index, 0, row.event_id)
            sheet.write_datetime(index, 1, row.occurred_at.astimezone(UTC), timestamp)
            # write_string never interprets text as a formula, so no prefixing is needed here.
            sheet.write_string(index, 2, row.actor_id)
            sheet.write_string(index, 3, row.area)
            sheet.write_string(index, 4, row.action)
            sheet.write_string(index, 5, row.entity_type)
            sheet.write_string(index, 6, row.entity_id)
            if detailed:
                sheet.write_string(index, 7, _details_text(row.details))
    finally:
        workbook.close()
