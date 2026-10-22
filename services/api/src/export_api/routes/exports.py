"""Export job endpoints (CONTRACT "Endpoints")."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from export_api.activity import DownloadAudit
from export_api.deps import ServicesDep, UserDep
from export_api.errors import ExportError
from exportkit.cursors import InvalidCursorError
from exportkit.ids import is_ulid, new_ulid
from exportkit.jobstore import new_job
from exportkit.models import (
    ALL_AREAS,
    Area,
    ExportRequest,
    ExportStageMessage,
    FailureCode,
    JobRecord,
    JobStatus,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/exports", tags=["exports"])

# Download audit rows need a concrete area; an all-areas export is filed under `accounts`
# (user-level actions) and the details record `export_area: all`.
_AUDIT_AREA_FOR_ALL = Area.ACCOUNTS


class JobList(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None


class SeenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=50)


class SeenResponse(BaseModel):
    ids: list[str]


class DownloadResponse(BaseModel):
    url: str
    file_name: str


def _owned(services: ServicesDep, user: str, export_id: str) -> JobRecord:
    job = services.jobs.get_for_owner(export_id, user) if is_ulid(export_id) else None
    if job is None:
        # Same answer for "does not exist" and "not yours": ids are not an oracle.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Export not found")
    return job


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=None)
def create_export(body: ExportRequest, services: ServicesDep, user: UserDep) -> JSONResponse:
    job = services.jobs.create(new_job(user, body, new_ulid()))
    location = f"/api/exports/{job.export_id}"
    try:
        services.queue.send(ExportStageMessage(export_id=job.export_id))
    except Exception:
        log.exception("queue send failed", extra={"export_id": job.export_id})
        failed = services.jobs.fail(job.export_id, FailureCode.QUEUE_SEND_FAILED) or job
        raise ExportError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Export could not be queued",
            FailureCode.QUEUE_SEND_FAILED,
            export=failed.public_view(),
        ) from None
    log.info("export queued", extra={"export_id": job.export_id, "area": job.area})
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=job.public_view(),
        headers={"Location": location},
    )


@router.get("", response_model=JobList)
def list_exports(
    services: ServicesDep,
    user: UserDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> JobList:
    try:
        jobs, next_cursor = services.jobs.list_for_owner(user, limit=limit, cursor=cursor)
    except InvalidCursorError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from None
    return JobList(items=[job.public_view() for job in jobs], next_cursor=next_cursor)


@router.post("/seen", response_model=SeenResponse)
def mark_seen(body: SeenRequest, services: ServicesDep, user: UserDep) -> SeenResponse:
    ids = [export_id for export_id in body.ids if is_ulid(export_id)]
    return SeenResponse(ids=services.jobs.mark_seen(user, ids))


@router.get("/{export_id}")
def get_export(export_id: str, services: ServicesDep, user: UserDep) -> dict[str, Any]:
    return _owned(services, user, export_id).public_view()


@router.post("/{export_id}/download", response_model=DownloadResponse)
def download_export(export_id: str, services: ServicesDep, user: UserDep) -> DownloadResponse:
    job = _owned(services, user, export_id)
    if job.status is not JobStatus.COMPLETED:
        raise ExportError(status.HTTP_409_CONFLICT, "Export not completed", FailureCode.NOT_READY)
    if job.expires_at <= time.time():
        raise ExportError(status.HTTP_410_GONE, "Export expired", FailureCode.EXPIRED)
    # Audit first (ADR-0004): if this insert fails no URL is ever handed out.
    services.activity.record_download(
        DownloadAudit(
            actor_id=user,
            area=_AUDIT_AREA_FOR_ALL if job.area == ALL_AREAS else Area(job.area),
            export_id=job.export_id,
            file_name=job.file_name,
            export_area=job.area,
        )
    )
    url = services.presigner.presign_download(
        job.object_key, job.file_name, services.presign_seconds
    )
    log.info("download issued", extra={"export_id": job.export_id})
    return DownloadResponse(url=url, file_name=job.file_name)
