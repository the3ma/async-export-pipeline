from __future__ import annotations

from typing import Any

import pytest
from api_fakes import FakeActivityRepository, sample_items
from fastapi.testclient import TestClient

from export_api.app import create_app
from export_api.config import ApiSettings
from export_api.services import Services
from exportkit.models import Action, Area


def test_activity_keyset_pages_cover_everything_once(
    client: TestClient, repo: FakeActivityRepository
) -> None:
    repo.items = sample_items(25)
    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 7}
        if cursor:
            params["cursor"] = cursor
        page = client.get("/api/activity", params=params).json()
        seen.extend(item["event_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(seen, reverse=True)
    assert sorted(seen) == list(range(1, 26))
    item = client.get("/api/activity", params={"limit": 1}).json()["items"][0]
    assert set(item) == {
        "event_id",
        "occurred_at",
        "actor_id",
        "area",
        "action",
        "entity_type",
        "entity_id",
    }


def test_activity_filters_are_forwarded(client: TestClient, repo: FakeActivityRepository) -> None:
    response = client.get(
        "/api/activity",
        params={
            "area": "billing",
            "action": "update",
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-02-01T00:00:00Z",
            "limit": 500,
        },
    )
    assert response.status_code == 200
    query = repo.queries[-1]
    assert (query.area, query.action, query.limit) == (Area.BILLING, Action.UPDATE, 500)
    assert query.from_ is not None and query.to is not None
    client.get("/api/activity", params={"area": "all"})
    assert repo.queries[-1].area is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"area": "nope"},
        {"action": "nope"},
        {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        {"from": "yesterday"},
    ],
)
def test_activity_rejects_bad_params(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/api/activity", params=params).status_code == 422


def test_activity_bad_cursor(client: TestClient) -> None:
    assert client.get("/api/activity", params={"cursor": "e30"}).status_code == 400


def test_activity_requires_identity(client: TestClient) -> None:
    assert client.get("/api/activity", headers={"X-User-Id": ""}).status_code == 401


def test_health_ok_and_degraded(client: TestClient, repo: FakeActivityRepository) -> None:
    ok = client.get("/api/health")
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"
    assert set(ok.json()["checks"]) == {"database", "s3"}
    repo.fail_ping = True
    degraded = client.get("/api/health")
    assert degraded.status_code == 503
    assert degraded.json()["checks"]["database"]["status"] == "error"
    assert "unavailable" not in degraded.text  # causes are logged, not echoed


def test_openapi_and_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/exports",
        "/api/exports/{export_id}",
        "/api/exports/seen",
        "/api/exports/{export_id}/download",
        "/api/activity",
        "/api/health",
    ):
        assert path in paths


def test_cors_allows_dev_ui(client: TestClient) -> None:
    response = client.options(
        "/api/exports",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-User-Id, Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    blocked = client.options(
        "/api/exports",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in blocked.headers


def test_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/api/health", headers={"X-Request-Id": "abc123"})
    assert response.headers["X-Request-Id"] == "abc123"


def test_jwt_mode_uses_authorizer_sub(services: Services) -> None:
    settings = ApiSettings(database_url="postgresql://unused", auth_mode="jwt")
    plain = TestClient(create_app(services=services, settings=settings))
    # Header identity is ignored in jwt mode.
    assert plain.get("/api/exports", headers={"X-User-Id": "alice"}).status_code == 401

    app = create_app(services=services, settings=settings)

    @app.middleware("http")
    async def fake_api_gateway(request: Any, call_next: Any) -> Any:
        request.scope["aws.event"] = {
            "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-from-jwt"}}}}
        }
        return await call_next(request)

    client = TestClient(app)
    response = client.post(
        "/api/exports", json={"area": "orders", "mode": "summary", "format": "csv"}
    )
    assert response.status_code == 202
    assert response.json()["owner_id"] == "user-from-jwt"


def test_lambda_handler_imports() -> None:
    from export_api import lambda_handler

    assert callable(lambda_handler.handler)
