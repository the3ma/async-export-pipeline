"""Activity grid endpoint: keyset-paginated, newest first."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import AwareDatetime

from export_api.activity import ActivityPage, ActivityQuery, decode_activity_cursor
from export_api.deps import ServicesDep, UserDep
from exportkit.cursors import InvalidCursorError
from exportkit.models import ALL_AREAS, Action, Area

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("", response_model=ActivityPage)
def list_activity(
    services: ServicesDep,
    _user: UserDep,
    area: Annotated[Area | Literal["all", ""] | None, Query()] = None,
    action: Annotated[Action | None, Query()] = None,
    from_: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    to: Annotated[AwareDatetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ActivityPage:
    if from_ and to and from_ > to:
        raise HTTPException(422, "'from' must not be after 'to'")
    try:
        after = decode_activity_cursor(cursor) if cursor else None
    except InvalidCursorError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from None
    query = ActivityQuery(
        area=None if area in (None, "", ALL_AREAS) else Area(area),
        action=action,
        from_=from_,
        to=to,
        limit=limit,
        after=after,
    )
    return services.activity.list_page(query)
