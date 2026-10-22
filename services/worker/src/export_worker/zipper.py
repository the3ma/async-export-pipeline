"""Assemble the parts of a multi-part export into one zip, streaming S3 -> zip -> S3.

``zipfile.ZipFile`` writes to the non-seekable :class:`S3MultipartWriter`; for unseekable outputs
the stdlib emits a data descriptor after each member instead of seeking back to patch the local
header, so nothing has to be buffered beyond the current upload part. Each part is read from S3 in
1 MiB chunks. CSV members are deflated, XLSX members are stored (already zip-compressed). The part
size is known from ``HeadObject`` up front, which lets ``zipfile`` decide on Zip64 per member; the
archive itself switches to Zip64 records automatically once it passes 4 GiB / 65,535 entries.
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from datetime import datetime
from typing import IO, Any, cast

from .s3io import MIB, S3MultipartWriter, iter_object


def build_zip(
    s3: Any,
    bucket: str,
    members: Sequence[tuple[str, str]],
    destination_key: str,
    *,
    timestamp: datetime,
    part_size: int = 8 * MIB,
) -> int:
    """Zip ``members`` (``(object_key, name_in_zip)``) into ``destination_key``.

    Returns the size of the archive in bytes.
    """
    date_time = timestamp.timetuple()[:6]
    with (
        S3MultipartWriter(
            s3, bucket, destination_key, part_size=part_size, content_type="application/zip"
        ) as out,
        zipfile.ZipFile(cast(IO[bytes], out), mode="w", allowZip64=True) as archive,
    ):
        for key, name in members:
            info = zipfile.ZipInfo(name, date_time=date_time)
            stored = name.endswith(".xlsx")
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            info.file_size = int(s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
            with archive.open(info, mode="w") as member:
                for chunk in iter_object(s3, bucket, key):
                    member.write(chunk)
    return out.tell()
