from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from exportkit import keys, naming
from exportkit.ids import is_ulid, new_ulid
from exportkit.models import ExportFormat, ExportMode

EXPORT_ID = "01JA2B3C4D5E6F7G8H9JKMNPQR"
AT = datetime(2026, 3, 7, 9, 5, 59, tzinfo=UTC)


def test_single_file_name_follows_contract() -> None:
    name = naming.file_name("billing", ExportMode.SUMMARY, ExportFormat.CSV, AT, EXPORT_ID)
    assert name == f"Activity_Billing_Summary_07-Mar-2026_0905_UTC_{EXPORT_ID}.csv"


def test_all_areas_detailed_xlsx() -> None:
    name = naming.file_name("all", ExportMode.DETAILED, ExportFormat.XLSX, AT, EXPORT_ID)
    assert name == f"Activity_All_Areas_Detailed_07-Mar-2026_0905_UTC_{EXPORT_ID}.xlsx"


def test_zip_and_part_names() -> None:
    assert naming.zip_name("orders", ExportMode.SUMMARY, AT, EXPORT_ID).endswith(
        f"_UTC_{EXPORT_ID}.zip"
    )
    part = naming.part_name("orders", ExportMode.SUMMARY, ExportFormat.CSV, AT, EXPORT_ID, 2, 5)
    assert part == f"Activity_Orders_Summary_07-Mar-2026_0905_UTC_{EXPORT_ID}_part02-of-05.csv"


@pytest.mark.parametrize(("part", "count"), [(0, 3), (4, 3)])
def test_part_out_of_range(part: int, count: int) -> None:
    with pytest.raises(ValueError, match="outside"):
        naming.part_name("orders", ExportMode.SUMMARY, ExportFormat.CSV, AT, EXPORT_ID, part, count)


def test_name_uses_utc_and_english_months() -> None:
    local = datetime(2026, 1, 1, 1, 30, tzinfo=timezone(timedelta(hours=3)))  # = 31 Dec 22:30 UTC
    name = naming.file_name("catalog", ExportMode.SUMMARY, ExportFormat.CSV, local, EXPORT_ID)
    assert "_31-Dec-2025_2230_UTC_" in name


def test_names_only_contain_safe_characters() -> None:
    name = naming.part_name("all", ExportMode.DETAILED, ExportFormat.XLSX, AT, EXPORT_ID, 1, 20)
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", name)


def test_object_key_uses_owner_hash() -> None:
    key = keys.object_key("user-42", EXPORT_ID, "a.csv")
    expected_hash = hashlib.sha256(b"user-42").hexdigest()[:16]
    assert key == f"exports/{expected_hash}/{EXPORT_ID}/a.csv"
    assert "user-42" not in key


def test_object_key_rejects_paths() -> None:
    with pytest.raises(ValueError, match="separators"):
        keys.object_key("u", EXPORT_ID, "../x.csv")


def test_ulids_are_valid_and_time_ordered() -> None:
    first, second = new_ulid(1_700_000_000_000), new_ulid(1_700_000_000_001)
    assert is_ulid(first) and is_ulid(second)
    assert first < second
    assert len({new_ulid() for _ in range(1000)}) == 1000
    assert not is_ulid("not-a-ulid")
