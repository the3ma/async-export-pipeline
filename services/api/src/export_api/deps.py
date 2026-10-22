"""FastAPI dependencies: the service bundle and the caller identity.

Two identity sources (``AUTH_MODE``):

* ``header`` (default, local demo): nginx injects a fixed ``X-User-Id``. Only safe behind a proxy
  that strips/overwrites the header from clients.
* ``jwt`` (AWS): the API Gateway HTTP API JWT authorizer has already verified the token; the
  identity is its ``sub`` claim from the Lambda event that Mangum exposes as
  ``request.scope["aws.event"]``. Client-sent ``X-User-Id`` headers are ignored in this mode.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from .services import Services

# Declared as a security scheme so /docs offers an "Authorize" box for the demo header.
_user_header = APIKeyHeader(name="X-User-Id", auto_error=False, description="Demo identity")
_USER_ID = re.compile(r"^[A-Za-z0-9._:@|-]{1,128}$")


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def _jwt_subject(request: Request) -> str | None:
    event: Any = request.scope.get("aws.event") or {}
    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    sub = claims.get("sub") if isinstance(claims, dict) else None
    return sub if isinstance(sub, str) else None


def current_user(
    request: Request, x_user_id: Annotated[str | None, Security(_user_header)] = None
) -> str:
    mode = getattr(request.app.state, "auth_mode", "header")
    user = _jwt_subject(request) if mode == "jwt" else x_user_id
    if not user or not _USER_ID.fullmatch(user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


ServicesDep = Annotated[Services, Depends(get_services)]
UserDep = Annotated[str, Depends(current_user)]
