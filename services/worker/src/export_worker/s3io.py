"""Streaming S3 I/O with bounded memory.

:class:`S3MultipartWriter` is a write-only, non-seekable file object. Bytes accumulate in one
buffer of ``part_size`` (>= 5 MiB, the S3 minimum for non-final parts); each full buffer becomes
one ``UploadPart`` call and is dropped. Peak memory is therefore about one part, independent of
the object size. Objects smaller than one part are sent with a single ``PutObject``.

The writer must be used as a context manager: a clean exit completes the upload, an exception
aborts it, so a failed stage never leaves a truncated object behind.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Any

MIB = 1024 * 1024
MIN_PART_SIZE = 5 * MIB


class S3MultipartWriter:
    def __init__(
        self,
        s3: Any,
        bucket: str,
        key: str,
        *,
        part_size: int = 8 * MIB,
        content_type: str = "application/octet-stream",
    ) -> None:
        if part_size < MIN_PART_SIZE:
            raise ValueError("S3 multipart parts must be at least 5 MiB")
        self._s3 = s3
        self._bucket = bucket
        self._key = key
        self._part_size = part_size
        self._content_type = content_type
        self._buffer = bytearray()
        self._parts: list[dict[str, Any]] = []
        self._upload_id: str | None = None
        self._position = 0
        self.closed = False

    # -- file-object protocol (enough for zipfile and the CSV writer) ------------------------

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def readable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._position

    def seek(self, *_: object) -> int:
        raise OSError("S3MultipartWriter is not seekable")

    def flush(self) -> None:
        """No-op: data is only sent in full parts (S3 rejects small non-final parts)."""

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self.closed:
            raise ValueError("write to closed S3MultipartWriter")
        size = len(data)
        self._buffer += data
        self._position += size
        while len(self._buffer) >= self._part_size:
            chunk = bytes(self._buffer[: self._part_size])
            del self._buffer[: self._part_size]
            self._upload_part(chunk)
        return size

    # -- lifecycle -----------------------------------------------------------------------------

    def __enter__(self) -> S3MultipartWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.abort()

    def commit(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._upload_id is None:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=self._key,
                Body=bytes(self._buffer),
                ContentType=self._content_type,
            )
        else:
            if self._buffer:
                self._upload_part(bytes(self._buffer))
            self._s3.complete_multipart_upload(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                MultipartUpload={"Parts": self._parts},
            )
        self._buffer.clear()

    def abort(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._buffer.clear()
        if self._upload_id is not None:
            self._s3.abort_multipart_upload(
                Bucket=self._bucket, Key=self._key, UploadId=self._upload_id
            )

    def _upload_part(self, chunk: bytes) -> None:
        if self._upload_id is None:
            response = self._s3.create_multipart_upload(
                Bucket=self._bucket, Key=self._key, ContentType=self._content_type
            )
            self._upload_id = str(response["UploadId"])
        number = len(self._parts) + 1
        response = self._s3.upload_part(
            Bucket=self._bucket,
            Key=self._key,
            UploadId=self._upload_id,
            PartNumber=number,
            Body=chunk,
        )
        self._parts.append({"ETag": response["ETag"], "PartNumber": number})


def iter_object(s3: Any, bucket: str, key: str, chunk_size: int = MIB) -> Iterator[bytes]:
    """Yield an object's bytes in ``chunk_size`` pieces (never the whole body at once)."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        yield from body.iter_chunks(chunk_size=chunk_size)
    finally:
        body.close()


def upload_file(
    s3: Any, bucket: str, key: str, path: str, *, part_size: int, content_type: str
) -> None:
    """Stream a local file to S3 through :class:`S3MultipartWriter`, one chunk at a time."""
    with (
        open(path, "rb") as source,
        S3MultipartWriter(s3, bucket, key, part_size=part_size, content_type=content_type) as out,
    ):
        while chunk := source.read(MIB):
            out.write(chunk)
