"""Worker configuration from the environment."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from exportkit.settings import ServiceSettings

MIB = 1024 * 1024


class WorkerSettings(ServiceSettings):
    # DATABASE_URL / DATABASE_SECRET_ARN (inherited) point at the read-only `exporter` role.
    part_rows: int = Field(default=1_000_000, ge=1, le=1_048_575)  # XLSX sheet limit minus header
    max_parts: int = Field(default=20, ge=1, le=99)
    fetch_size: int = Field(default=5_000, ge=100)  # server-side cursor batch (itersize)
    s3_part_size: int = Field(default=8 * MIB, ge=5 * MIB)  # multipart chunk = memory bound
    max_receive_count: int = Field(default=3, ge=1)  # must match the queue's redrive policy
    tmp_dir: str | None = None  # XLSX scratch space (Lambda: /tmp)
    mail_transport: Literal["smtp", "ses"] = "smtp"  # ses: SESv2 SendEmail with raw content
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_timeout: float = 10.0
    mail_from: str = "exports@example.test"
    # Demo recipient mapping: <owner_id>@<MAIL_TO_DOMAIN>. A real system would look the
    # address up in its user directory; the log itself only holds pseudonymous ids.
    mail_to_domain: str = "example.test"
    app_base_url: str = "http://localhost:8080"
    poll_wait_seconds: int = Field(default=20, ge=0, le=20)
    # Poller only: visibility timeout applied to a failed message (None = keep the queue's).
    retry_delay_seconds: int | None = Field(default=15, ge=0, le=43_200)
