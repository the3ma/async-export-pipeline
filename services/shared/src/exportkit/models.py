"""Pydantic models that mirror docs/CONTRACT.md."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Area(StrEnum):
    ACCOUNTS = "accounts"
    BILLING = "billing"
    CATALOG = "catalog"
    ORDERS = "orders"
    SETTINGS = "settings"


class Action(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    VIEW = "view"
    EXPORT = "export"
    SIGN_IN = "sign_in"


ALL_AREAS = "all"
ExportArea = Literal["accounts", "billing", "catalog", "orders", "settings", "all"]


class ExportMode(StrEnum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_final(self) -> bool:
        return self in (JobStatus.COMPLETED, JobStatus.FAILED)


class FailureCode(StrEnum):
    QUEUE_SEND_FAILED = "QUEUE_SEND_FAILED"
    TOO_LARGE = "TOO_LARGE"
    ROWSET_CHANGED = "ROWSET_CHANGED"
    WORKER_ERROR = "WORKER_ERROR"
    EXPIRED = "EXPIRED"  # response only, never stored
    NOT_READY = "NOT_READY"  # response only, never stored


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExportFilters(_Strict):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    from_: AwareDatetime | None = Field(default=None, alias="from")
    to: AwareDatetime | None = None
    action: Action | None = None

    @model_validator(mode="after")
    def _ordered(self) -> ExportFilters:
        if self.from_ and self.to and self.from_ > self.to:
            raise ValueError("'from' must not be after 'to'")
        return self

    def as_record(self) -> dict[str, str]:
        """JSON-friendly representation stored on the job (only the filters that are set)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class ExportRequest(_Strict):
    area: ExportArea
    mode: ExportMode
    format: ExportFormat
    filters: ExportFilters = Field(default_factory=ExportFilters)


class KeysetCursor(_Strict):
    """Position after which a keyset read continues (``occurred_at DESC, event_id DESC``)."""

    occurred_at: AwareDatetime
    event_id: int


class JobRecord(BaseModel):
    """One export job as stored in DynamoDB (and, minus internal fields, returned by the API)."""

    model_config = ConfigDict(extra="ignore")

    export_id: str
    owner_id: str
    area: ExportArea
    mode: ExportMode
    format: ExportFormat
    filters: ExportFilters = Field(default_factory=ExportFilters)
    status: JobStatus
    requested_at: str
    started_at: str | None = None
    completed_at: str | None = None
    high_water_event_id: int | None = None
    row_count: int | None = None
    part_count: int | None = None
    parts_done: int | None = None
    file_name: str
    object_key: str
    failure_code: FailureCode | None = None
    expires_at: int
    notified_at: str | None = None
    seen_at: str | None = None
    # Internal bookkeeping (not part of the public job view):
    expected_rows: int | None = None  # the count pinned with the high-water mark
    next_cursor: KeysetCursor | None = None  # where part ``parts_done + 1`` starts

    INTERNAL_FIELDS: ClassVar[frozenset[str]] = frozenset({"expected_rows", "next_cursor"})

    @property
    def is_multipart(self) -> bool:
        return (self.part_count or 0) > 1

    def to_item(self) -> dict[str, Any]:
        """DynamoDB item (``None`` attributes omitted)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)

    def public_view(self) -> dict[str, Any]:
        """API representation: every contract field (``null`` when unset), filters as requested."""
        view = self.model_dump(mode="json", by_alias=True, exclude=set(self.INTERNAL_FIELDS))
        view["filters"] = self.filters.as_record()
        return view


class ExportStageMessage(_Strict):
    export_id: str
    stage: Literal["export"] = "export"


class PartStageMessage(_Strict):
    export_id: str
    stage: Literal["part"] = "part"
    part: int = Field(ge=1)
    cursor: KeysetCursor | None = None


class ZipStageMessage(_Strict):
    export_id: str
    stage: Literal["zip"] = "zip"


StageMessage = ExportStageMessage | PartStageMessage | ZipStageMessage
QueueMessage = Annotated[StageMessage, Field(discriminator="stage")]
_QUEUE_MESSAGE: TypeAdapter[StageMessage] = TypeAdapter(QueueMessage)


def parse_queue_message(body: str | bytes) -> StageMessage:
    return _QUEUE_MESSAGE.validate_json(body)


def dump_queue_message(message: StageMessage) -> str:
    return message.model_dump_json()
