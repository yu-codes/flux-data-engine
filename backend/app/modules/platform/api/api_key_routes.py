"""API keys API.

Managing keys is administration, so every route here needs an administrator.
Using one is a different matter and happens in `deps.py`, where a presented key
becomes a scope like any other credential.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import ApiKeyServiceDep, ScopeDep
from app.api.schema_base import ApiModel
from app.api.security import AdminUser

from ..domain.api_keys import ApiKey

router = APIRouter(tags=["api-keys"])


class ApiKeyCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    can_write: bool = Field(
        default=False,
        description=(
            "Whether this key may change anything. Off by default: a key "
            "usually exists to call a model, and most never need more."
        ),
    )
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyOut(ApiModel):
    id: str
    name: str
    workspace_id: str
    #  The visible prefix, so a key can be recognised in a list without being
    #  usable from it.
    hint: str
    can_write: bool
    is_active: bool
    created_by: str | None
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyIssued(ApiKeyOut):
    #  Present exactly once, in the response to the request that created it.
    #  Nothing stores the plaintext, so this is the only time it exists.
    secret: str


def _out(key: ApiKey) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "workspace_id": key.workspace_id,
        "hint": key.hint,
        "can_write": key.can_write,
        "is_active": key.is_active,
        "created_by": key.created_by,
        "last_used_at": key.last_used_at,
        "expires_at": key.expires_at,
        "revoked_at": key.revoked_at,
        "created_at": key.created_at,
    }


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(service: ApiKeyServiceDep, scope: ScopeDep, user: AdminUser):
    return [ApiKeyOut(**_out(k)) for k in service.list(scope.workspace_id)]


@router.post(
    "/api-keys",
    response_model=ApiKeyIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a key. The secret is shown once and never again.",
)
def issue_api_key(
    payload: ApiKeyCreate,
    service: ApiKeyServiceDep,
    scope: ScopeDep,
    user: AdminUser,
):
    key, secret = service.issue(
        name=payload.name,
        workspace_id=scope.workspace_id or "",
        can_write=payload.can_write,
        expires_in_days=payload.expires_in_days,
        created_by=user.id,
    )
    return ApiKeyIssued(**_out(key), secret=secret)


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(key_id: str, service: ApiKeyServiceDep, user: AdminUser):
    """Revoked rather than deleted: the record of what existed is worth keeping."""
    return ApiKeyOut(**_out(service.revoke(key_id)))


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(key_id: str, service: ApiKeyServiceDep, user: AdminUser) -> None:
    service.delete(key_id)
