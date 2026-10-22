"""Completion / failure e-mail.

The message only says that an export finished and links to the exports page of the app. It never
contains the file, its object key or a (pre-signed) download link: downloads always go through the
audited ``POST /api/exports/{id}/download`` (ADR-0005).
"""

from __future__ import annotations

import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any, Protocol

from exportkit.models import JobRecord, JobStatus
from exportkit.naming import area_label


class Notifier(Protocol):
    def send(self, job: JobRecord) -> None: ...


def compose(job: JobRecord, *, sender: str, recipient: str, app_base_url: str) -> EmailMessage:
    link = app_base_url.rstrip("/") + "/exports"
    what = (
        f"{area_label(job.area).replace('_', ' ')} activity ({job.mode.value}, {job.format.value})"
    )
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    if job.status is JobStatus.COMPLETED:
        message["Subject"] = "Your activity export is ready"
        body = (
            f"Your export of {what} has finished"
            f" ({job.row_count or 0:,} rows).\n\n"
            f"Open {link} to download it. The file is kept for 7 days."
        )
    else:
        message["Subject"] = "Your activity export failed"
        body = (
            f"Your export of {what} could not be completed"
            f" (reason: {job.failure_code.value if job.failure_code else 'unknown'}).\n\n"
            f"Open {link} to see the details or start a new export."
        )
    message.set_content(body + f"\n\nExport id: {job.export_id}\n")
    return message


class SmtpNotifier:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        app_base_url: str,
        recipient_for: Callable[[str], str],
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._app_base_url = app_base_url
        self._recipient_for = recipient_for
        self._timeout = timeout

    def send(self, job: JobRecord) -> None:
        message = compose(
            job,
            sender=self._sender,
            recipient=self._recipient_for(job.owner_id),
            app_base_url=self._app_base_url,
        )
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.send_message(message)


class SesNotifier:
    """Same message as :class:`SmtpNotifier`, sent through SESv2 ``SendEmail`` (raw content)."""

    def __init__(
        self,
        *,
        ses_client: Any,
        sender: str,
        app_base_url: str,
        recipient_for: Callable[[str], str],
    ) -> None:
        self._ses = ses_client
        self._sender = sender
        self._app_base_url = app_base_url
        self._recipient_for = recipient_for

    def send(self, job: JobRecord) -> None:
        recipient = self._recipient_for(job.owner_id)
        message = compose(
            job, sender=self._sender, recipient=recipient, app_base_url=self._app_base_url
        )
        self._ses.send_email(
            FromEmailAddress=self._sender,
            Destination={"ToAddresses": [recipient]},
            Content={"Raw": {"Data": message.as_bytes()}},
        )
