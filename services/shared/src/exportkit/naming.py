"""Export file names (CONTRACT "File naming").

``Activity_<Area>_<Summary|Detailed>_<DD-MMM-YYYY>_<HHmm>_UTC_<exportId>.<csv|xlsx|zip>``
and parts ``..._part01-of-05.<ext>``. Only enum values, the request time and the generated id
go into a name - never user-supplied text.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import ALL_AREAS, ExportFormat, ExportMode

# Fixed English month abbreviations: names must not depend on the host locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def area_label(area: str) -> str:
    return "All_Areas" if area == ALL_AREAS else area.title()


def _stem(area: str, mode: ExportMode, requested_at: datetime, export_id: str) -> str:
    at = requested_at.astimezone(UTC)
    stamp = f"{at.day:02d}-{_MONTHS[at.month - 1]}-{at.year:04d}_{at.hour:02d}{at.minute:02d}"
    return f"Activity_{area_label(area)}_{mode.value.title()}_{stamp}_UTC_{export_id}"


def file_name(
    area: str, mode: ExportMode, fmt: ExportFormat, requested_at: datetime, export_id: str
) -> str:
    """Name of a single-file export."""
    return f"{_stem(area, mode, requested_at, export_id)}.{fmt.value}"


def zip_name(area: str, mode: ExportMode, requested_at: datetime, export_id: str) -> str:
    """Name of the final archive of a multi-part export."""
    return f"{_stem(area, mode, requested_at, export_id)}.zip"


def part_name(
    area: str,
    mode: ExportMode,
    fmt: ExportFormat,
    requested_at: datetime,
    export_id: str,
    part: int,
    part_count: int,
) -> str:
    """Name of part ``part`` of ``part_count`` (inside the zip and as the temporary S3 object)."""
    if not 1 <= part <= part_count:
        raise ValueError(f"part {part} outside 1..{part_count}")
    stem = _stem(area, mode, requested_at, export_id)
    return f"{stem}_part{part:02d}-of-{part_count:02d}.{fmt.value}"
