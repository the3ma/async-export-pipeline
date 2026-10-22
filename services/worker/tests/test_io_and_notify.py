from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any

import pytest
from worker_fakes import make_row

from export_worker.notify import SesNotifier, compose
from export_worker.poller import poll_once, to_lambda_event
from export_worker.s3io import MIB, S3MultipartWriter, iter_object
from export_worker.writers import write_csv
from export_worker.zipper import build_zip
from exportkit.ids import new_ulid
from exportkit.jobstore import new_job
from exportkit.models import ExportRequest, FailureCode, JobStatus

BUCKET = "exports"


def test_small_object_uses_single_put(s3: Any) -> None:
    with S3MultipartWriter(s3, BUCKET, "k/small", part_size=5 * MIB) as out:
        out.write(b"hello ")
        out.write(b"world")
    assert s3.get_object(Bucket=BUCKET, Key="k/small")["Body"].read() == b"hello world"


def test_large_object_streams_in_parts(s3: Any) -> None:
    chunk = bytes(range(256)) * 4096  # 1 MiB
    with S3MultipartWriter(s3, BUCKET, "k/large", part_size=5 * MIB) as out:
        for _ in range(11):
            out.write(chunk)
            assert len(out._buffer) < 5 * MIB  # memory bounded by one part
    head = s3.head_object(Bucket=BUCKET, Key="k/large", PartNumber=1)
    assert head["PartsCount"] == 3
    assert b"".join(iter_object(s3, BUCKET, "k/large")) == chunk * 11


def test_failed_write_aborts_upload(s3: Any) -> None:
    def write_then_crash() -> None:
        with S3MultipartWriter(s3, BUCKET, "k/broken", part_size=5 * MIB) as out:
            out.write(b"x" * (6 * MIB))  # one part already uploaded
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        write_then_crash()
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)
    assert s3.list_multipart_uploads(Bucket=BUCKET).get("Uploads", []) == []


def test_part_size_floor() -> None:
    with pytest.raises(ValueError, match="5 MiB"):
        S3MultipartWriter(None, BUCKET, "k", part_size=MIB)


def test_zip_of_streamed_members(s3: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key="p/1.csv", Body=b"a,b\r\n1,2\r\n")
    s3.put_object(Bucket=BUCKET, Key="p/2.xlsx", Body=b"PK-fake-xlsx")
    from datetime import UTC, datetime

    size = build_zip(
        s3,
        BUCKET,
        [("p/1.csv", "one.csv"), ("p/2.xlsx", "two.xlsx")],
        "p/all.zip",
        timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )
    data = s3.get_object(Bucket=BUCKET, Key="p/all.zip")["Body"].read()
    assert len(data) == size
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.testzip() is None
    assert archive.read("one.csv") == b"a,b\r\n1,2\r\n"
    assert archive.getinfo("two.xlsx").compress_type == zipfile.ZIP_STORED
    assert archive.getinfo("one.csv").date_time == (2026, 1, 2, 3, 4, 4)  # DOS 2-second precision


def test_csv_neutralises_formula_injection() -> None:
    row = make_row(1)
    hostile = type(row)(1, row.occurred_at, "=HYPERLINK(1)", "billing", "update", "+x", "@y", None)
    sink = io.BytesIO()
    write_csv([hostile], sink, detailed=False)
    values = list(csv.reader(io.StringIO(sink.getvalue().decode("utf-8-sig"))))[1]
    assert values[2] == "'=HYPERLINK(1)" and values[5] == "'+x" and values[6] == "'@y"


def test_csv_flushes_incrementally() -> None:
    writes: list[int] = []

    class Sink:
        def write(self, data: bytes) -> int:
            writes.append(len(data))
            return len(data)

    write_csv((make_row(i) for i in range(1, 2001)), Sink(), detailed=True, flush_bytes=4096)
    assert len(writes) > 10 and max(writes) < 8192


def _job(status: JobStatus, **extra: Any) -> Any:
    request = ExportRequest(area="all", mode="summary", format="csv")
    job = new_job("alice", request, new_ulid())
    return job.model_copy(update={"status": status, **extra})


def test_email_links_to_exports_page_only() -> None:
    job = _job(JobStatus.COMPLETED, row_count=1234)
    message = compose(
        job, sender="from@x.test", recipient="alice@x.test", app_base_url="http://app/"
    )
    body = message.get_content()
    assert "http://app/exports" in body
    assert message["Subject"] == "Your activity export is ready"
    assert job.object_key not in body and job.file_name not in body
    assert body.count("http") == 1  # no other link, never a download URL
    assert "1,234 rows" in body


def test_failure_email_mentions_reason() -> None:
    job = _job(JobStatus.FAILED, failure_code=FailureCode.TOO_LARGE)
    message = compose(job, sender="f@x.test", recipient="a@x.test", app_base_url="http://app")
    assert message["Subject"] == "Your activity export failed"
    assert "TOO_LARGE" in message.get_content()


def test_ses_notifier_sends_raw_message() -> None:
    calls: list[dict[str, Any]] = []

    class FakeSes:
        def send_email(self, **kwargs: Any) -> dict[str, str]:
            calls.append(kwargs)
            return {"MessageId": "1"}

    notifier = SesNotifier(
        ses_client=FakeSes(),
        sender="exports@x.test",
        app_base_url="https://app.x.test",
        recipient_for=lambda owner: f"{owner}@x.test",
    )
    notifier.send(_job(JobStatus.COMPLETED, row_count=1))
    assert calls[0]["FromEmailAddress"] == "exports@x.test"
    assert calls[0]["Destination"] == {"ToAddresses": ["alice@x.test"]}
    assert b"https://app.x.test/exports" in calls[0]["Content"]["Raw"]["Data"]


def test_poller_deletes_only_successful_messages(sqs: Any, queue_url: str) -> None:
    from export_worker.config import WorkerSettings
    from export_worker.stages import Outcome

    class Worker:
        def process(self, message: Any) -> Outcome:
            if message.export_id == "bad":
                raise RuntimeError("fail")
            return Outcome.NOOP

        def give_up(self, export_id: str) -> None:
            raise AssertionError("not on first receive")

    settings = WorkerSettings(database_url="postgresql://unused", poll_wait_seconds=0)
    for export_id in ("good", "bad"):
        sqs.send_message(
            QueueUrl=queue_url, MessageBody=json.dumps({"export_id": export_id, "stage": "zip"})
        )
    assert poll_once(sqs, queue_url, Worker(), settings) == 1  # type: ignore[arg-type]
    assert poll_once(sqs, queue_url, Worker(), settings) == 1  # type: ignore[arg-type]
    attrs = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["All"])["Attributes"]
    assert attrs["ApproximateNumberOfMessages"] == "0"
    assert attrs["ApproximateNumberOfMessagesNotVisible"] == "1"  # the failed one, for a retry


def test_lambda_event_shape() -> None:
    event = to_lambda_event(
        [{"MessageId": "1", "ReceiptHandle": "r", "Body": "{}", "Attributes": {"A": "1"}}]
    )
    assert event["Records"][0]["messageId"] == "1"
    assert event["Records"][0]["attributes"] == {"A": "1"}
