from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

from api_fakes import FakeActivityRepository, RecordingPresigner

from export_api.app import create_app
from export_api.config import ApiSettings
from export_api.services import Services
from exportkit.jobstore import JobStore
from exportkit.queue import SqsSender


@pytest.fixture
def repo() -> FakeActivityRepository:
    return FakeActivityRepository()


@pytest.fixture
def services(store: JobStore, repo: FakeActivityRepository, sqs: Any, queue_url: str) -> Services:
    return Services(
        activity=repo,
        jobs=store,
        queue=SqsSender(sqs, queue_url),
        presigner=RecordingPresigner(repo),
        presign_seconds=60,
        health_checks={"database": repo.ping, "s3": lambda: None},
    )


@pytest.fixture
def client(services: Services) -> TestClient:
    app = create_app(services=services, settings=ApiSettings(database_url="postgresql://unused"))
    return TestClient(app, headers={"X-User-Id": "alice"}, raise_server_exceptions=False)
