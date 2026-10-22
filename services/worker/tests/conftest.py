from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from worker_fakes import BUCKET, MAX_PARTS, PART_ROWS, FakeRowSource, Harness, RecordingNotifier

from export_worker.stages import ExportWorker
from exportkit.jobstore import JobStore
from exportkit.queue import SqsSender


@pytest.fixture
def make_harness(
    store: JobStore, s3: Any, sqs: Any, queue_url: str, drain: Any, tmp_path: Path
) -> Callable[..., Harness]:
    def factory(rows: FakeRowSource, **overrides: Any) -> Harness:
        notifier = RecordingNotifier()
        options: dict[str, Any] = {
            "part_rows": PART_ROWS,
            "max_parts": MAX_PARTS,
            "tmp_dir": str(tmp_path),
            **overrides,
        }
        worker = ExportWorker(
            jobs=store,
            rows=rows,
            s3=s3,
            bucket=BUCKET,
            queue=SqsSender(sqs, queue_url),
            notifier=notifier,
            **options,
        )
        return Harness(worker, rows, notifier, store, s3, sqs, queue_url, drain)

    return factory
