"""Sending stage messages to the ``export-requests`` queue."""

from __future__ import annotations

from typing import Any, Protocol

from .models import StageMessage, dump_queue_message


class MessageSender(Protocol):
    def send(self, message: StageMessage) -> str: ...


class SqsSender:
    def __init__(self, sqs_client: Any, queue_url: str) -> None:
        self._sqs = sqs_client
        self._queue_url = queue_url

    def send(self, message: StageMessage) -> str:
        response = self._sqs.send_message(
            QueueUrl=self._queue_url, MessageBody=dump_queue_message(message)
        )
        return str(response["MessageId"])
