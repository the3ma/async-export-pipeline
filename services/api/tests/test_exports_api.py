from __future__ import annotations

import json
import time
from typing import Any

import pytest
from api_fakes import FailingSender, FakeActivityRepository, RecordingPresigner
from fastapi.testclient import TestClient

from export_api.services import Services
from exportkit.ids import new_ulid
from exportkit.jobstore import JobStore
from exportkit.models import Area, JobStatus

BODY = {
    "area": "billing",
    "mode": "summary",
    "format": "csv",
    "filters": {"from": "2026-01-01T00:00:00Z", "to": "2026-06-30T23:59:59Z", "action": "update"},
}


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/exports", json={**BODY, **overrides})
    assert response.status_code == 202, response.text
    return dict(response.json())


def complete(store: JobStore, export_id: str, rows: int = 10) -> None:
    assert store.claim(export_id, high_water_event_id=100)
    job = store.get(export_id)
    assert job is not None
    assert store.plan(
        export_id,
        expected_rows=rows,
        part_count=1,
        file_name=job.file_name,
        object_key=job.object_key,
    )
    assert store.complete(export_id, row_count=rows)


def test_post_queues_job_and_message(client: TestClient, sqs: Any, queue_url: str) -> None:
    response = client.post("/api/exports", json=BODY)
    assert response.status_code == 202
    job = response.json()
    assert response.headers["Location"] == f"/api/exports/{job['export_id']}"
    assert job["status"] == "queued"
    assert job["owner_id"] == "alice"
    assert job["filters"] == BODY["filters"]
    assert job["file_name"].startswith("Activity_Billing_Summary_")
    assert "url" not in json.dumps(job).lower().replace("queue", "")
    messages = sqs.receive_message(QueueUrl=queue_url, WaitTimeSeconds=0)["Messages"]
    assert json.loads(messages[0]["Body"]) == {"export_id": job["export_id"], "stage": "export"}


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "unexpected": True},
        {**BODY, "filters": {"actor_id": "x"}},
        {**BODY, "area": "everything"},
        {**BODY, "format": "pdf"},
    ],
)
def test_post_rejects_unknown_or_invalid_fields(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/exports", json=body).status_code == 422


def test_requires_identity(client: TestClient) -> None:
    for header in ({"X-User-Id": ""}, {"X-User-Id": "bad id with spaces"}):
        response = client.get("/api/exports", headers=header)
        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}


def test_queue_failure_marks_job_failed(services: Services, client: TestClient) -> None:
    services.queue = FailingSender()
    response = client.post("/api/exports", json=BODY)
    assert response.status_code == 503
    body = response.json()
    assert body["failure_code"] == "QUEUE_SEND_FAILED"
    stored = services.jobs.get(body["export"]["export_id"])
    assert stored is not None and stored.status is JobStatus.FAILED
    assert stored.failure_code is not None and stored.failure_code.value == "QUEUE_SEND_FAILED"


def test_get_is_owner_only(client: TestClient) -> None:
    job = create(client)
    mine = client.get(f"/api/exports/{job['export_id']}")
    assert mine.status_code == 200 and mine.json()["export_id"] == job["export_id"]
    assert "next_cursor" not in mine.json()
    theirs = client.get(f"/api/exports/{job['export_id']}", headers={"X-User-Id": "mallory"})
    assert theirs.status_code == 404
    assert client.get(f"/api/exports/{new_ulid()}").status_code == 404
    assert client.get("/api/exports/not-a-ulid").status_code == 404


def test_list_newest_first_paginated(client: TestClient) -> None:
    ids = []
    for _ in range(3):
        ids.append(create(client)["export_id"])
        time.sleep(0.002)  # distinct requested_at milliseconds
    client.post("/api/exports", json=BODY, headers={"X-User-Id": "bob"})

    first = client.get("/api/exports", params={"limit": 2}).json()
    assert [j["export_id"] for j in first["items"]] == ids[::-1][:2]
    assert first["next_cursor"]
    second = client.get("/api/exports", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [j["export_id"] for j in second["items"]] == [ids[0]]
    assert second["next_cursor"] is None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}])
def test_list_limit_bounds(client: TestClient, params: dict[str, int]) -> None:
    assert client.get("/api/exports", params=params).status_code == 422


def test_list_invalid_cursor(client: TestClient) -> None:
    response = client.get("/api/exports", params={"cursor": "!!!"})
    assert response.status_code == 400


def test_seen_marks_only_own_jobs(client: TestClient, store: JobStore) -> None:
    mine = create(client)["export_id"]
    theirs = client.post("/api/exports", json=BODY, headers={"X-User-Id": "bob"}).json()
    response = client.post("/api/exports/seen", json={"ids": [mine, theirs["export_id"], "junk"]})
    assert response.status_code == 200
    assert response.json() == {"ids": [mine]}
    mine_job, their_job = store.get(mine), store.get(theirs["export_id"])
    assert mine_job is not None and mine_job.seen_at
    assert their_job is not None and their_job.seen_at is None
    assert client.post("/api/exports/seen", json={"ids": []}).status_code == 422


def test_download_not_ready(client: TestClient, repo: FakeActivityRepository) -> None:
    job = create(client)
    response = client.post(f"/api/exports/{job['export_id']}/download")
    assert response.status_code == 409
    assert response.json() == {"detail": "Export not completed", "failure_code": "NOT_READY"}
    assert repo.audits == []


def test_download_writes_audit_before_presigning(
    client: TestClient, store: JobStore, repo: FakeActivityRepository, services: Services
) -> None:
    job = create(client)
    complete(store, job["export_id"])
    response = client.post(f"/api/exports/{job['export_id']}/download")
    assert response.status_code == 200
    body = response.json()
    assert body["file_name"] == job["file_name"]
    assert "X-Amz-Expires=60" in body["url"]
    presigner = services.presigner
    assert isinstance(presigner, RecordingPresigner)
    assert presigner.audits_at_call == [1]  # audit row existed when the URL was minted
    audit = repo.audits[0]
    assert (audit.actor_id, audit.area, audit.export_id) == (
        "alice",
        Area.BILLING,
        job["export_id"],
    )
    # The status endpoint still never exposes a URL.
    status_body = client.get(f"/api/exports/{job['export_id']}").json()
    assert body["url"] not in json.dumps(status_body)


def test_download_audit_failure_returns_no_url(
    client: TestClient, store: JobStore, repo: FakeActivityRepository, services: Services
) -> None:
    job = create(client)
    complete(store, job["export_id"])
    repo.fail_audit = True
    response = client.post(f"/api/exports/{job['export_id']}/download")
    assert response.status_code == 500
    assert "url" not in response.text
    presigner = services.presigner
    assert isinstance(presigner, RecordingPresigner)
    assert presigner.calls == []


def test_download_all_areas_audit_area(
    client: TestClient, store: JobStore, repo: FakeActivityRepository
) -> None:
    job = create(client, area="all")
    complete(store, job["export_id"])
    assert client.post(f"/api/exports/{job['export_id']}/download").status_code == 200
    assert repo.audits[0].area is Area.ACCOUNTS
    assert repo.audits[0].export_area == "all"


def test_download_expired(client: TestClient, store: JobStore, table: Any) -> None:
    job = create(client)
    complete(store, job["export_id"])
    table.update_item(
        Key={"export_id": job["export_id"]},
        UpdateExpression="SET expires_at = :past",
        ExpressionAttributeValues={":past": int(time.time()) - 1},
    )
    response = client.post(f"/api/exports/{job['export_id']}/download")
    assert response.status_code == 410
    assert response.json() == {"detail": "Export expired", "failure_code": "EXPIRED"}


def test_download_other_owner_is_404(
    client: TestClient, store: JobStore, repo: FakeActivityRepository
) -> None:
    job = create(client)
    complete(store, job["export_id"])
    response = client.post(
        f"/api/exports/{job['export_id']}/download", headers={"X-User-Id": "mallory"}
    )
    assert response.status_code == 404
    assert repo.audits == []
