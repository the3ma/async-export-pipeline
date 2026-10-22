from __future__ import annotations

import json
from typing import Any

import boto3
import pytest
from pydantic import ValidationError

from exportkit.cursors import InvalidCursorError, decode_cursor, encode_cursor
from exportkit.models import (
    ExportRequest,
    PartStageMessage,
    ZipStageMessage,
    dump_queue_message,
    parse_queue_message,
)
from exportkit.settings import (
    ConfigurationError,
    ServiceSettings,
    database_url_from_secret,
)


def test_export_request_accepts_contract_example() -> None:
    request = ExportRequest.model_validate(
        {
            "area": "billing",
            "mode": "summary",
            "format": "csv",
            "filters": {
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-06-30T23:59:59Z",
                "action": "update",
            },
        }
    )
    assert request.filters.as_record() == {
        "from": "2026-01-01T00:00:00Z",
        "to": "2026-06-30T23:59:59Z",
        "action": "update",
    }


@pytest.mark.parametrize(
    "body",
    [
        {"area": "billing", "mode": "summary", "format": "csv", "extra": 1},
        {"area": "billing", "mode": "summary", "format": "csv", "filters": {"user": "x"}},
        {"area": "nope", "mode": "summary", "format": "csv"},
        {"area": "billing", "mode": "full", "format": "csv"},
        {"area": "billing", "mode": "summary", "format": "pdf"},
        {"area": "billing", "mode": "summary", "format": "csv", "filters": {"from": "2026-01-01"}},
        {
            "area": "billing",
            "mode": "summary",
            "format": "csv",
            "filters": {"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        },
    ],
)
def test_export_request_rejects_invalid(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ExportRequest.model_validate(body)


def test_queue_messages_round_trip() -> None:
    part = parse_queue_message(
        '{"export_id":"X","stage":"part","part":2,'
        '"cursor":{"occurred_at":"2026-01-01T00:00:00Z","event_id":123}}'
    )
    assert isinstance(part, PartStageMessage)
    assert part.cursor is not None and part.cursor.event_id == 123
    assert parse_queue_message(dump_queue_message(part)) == part
    assert isinstance(parse_queue_message('{"export_id":"X","stage":"zip"}'), ZipStageMessage)
    with pytest.raises(ValidationError):
        parse_queue_message('{"export_id":"X","stage":"bogus"}')


def test_cursor_round_trip_and_garbage() -> None:
    token = encode_cursor({"a": 1, "b": "x"})
    assert "=" not in token
    assert decode_cursor(token) == {"a": 1, "b": "x"}
    for bad in ("%%%", "bm90LWpzb24", "WzEsMl0"):  # not base64 / not json / not an object
        with pytest.raises(InvalidCursorError):
            decode_cursor(bad)


def test_database_url_prefers_explicit_value() -> None:
    settings = ServiceSettings(database_url="postgresql://a:b@h/db", database_secret_arn="arn")
    assert settings.resolve_database_url() == "postgresql://a:b@h/db"


def test_database_url_requires_some_configuration() -> None:
    with pytest.raises(ConfigurationError):
        ServiceSettings(database_url=None, database_secret_arn=None).resolve_database_url()


def test_database_url_from_rds_secret(aws: None) -> None:
    client = boto3.client("secretsmanager", region_name="us-east-1")
    arn = client.create_secret(
        Name="db",
        SecretString=json.dumps(
            {
                "engine": "postgres",
                "host": "db.internal",
                "port": 5432,
                "dbname": "activity",
                "username": "app",
                "password": "p@ss/word:1",
            }
        ),
    )["ARN"]
    settings = ServiceSettings(database_url=None, database_secret_arn=arn)
    assert (
        settings.resolve_database_url(client)
        == "postgresql://app:p%40ss%2Fword%3A1@db.internal:5432/activity"
    )
    # Without an injected client the settings build their own (still mocked by moto).
    assert settings.resolve_database_url().startswith("postgresql://app:")


def test_secret_url_key_wins_and_bad_secret_is_reported() -> None:
    assert (
        database_url_from_secret({"url": "postgresql://x", "host": "ignored"}) == "postgresql://x"
    )
    with pytest.raises(ConfigurationError):
        database_url_from_secret({"host": "h"})
