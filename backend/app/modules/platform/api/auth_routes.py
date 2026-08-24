"""Authentication endpoints.

This router is mounted without a module guard: signing in is what produces the
token the guards need. Everything except `/auth/login` still requires a valid
token via `CurrentUser`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import AuditServiceDep, AuthServiceDep
from app.api.schema_base import ApiModel
from app.api.security import AdminUser, CurrentUser
from app.core.config import get_settings

from ..domain.entities import Permission, Role, User

router = APIRouter(tags=["auth"])


class LoginRequest(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)


class TokenOut(ApiModel):
    access_token: str
    token_type: str
    expires_in: int
    user: UserOut


class UserOut(ApiModel):
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    permissions: list[str]
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(ApiModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8)
    role: str = Role.VIEWER.value
    display_name: str = ""


class UserUpdate(ApiModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class PasswordChange(ApiModel):
    current_password: str
    new_password: str = Field(min_length=8)


# --------------------------------------------------------------------------
# sign in
# --------------------------------------------------------------------------
@router.post(
    "/auth/login",
    response_model=TokenOut,
    summary="Exchange credentials for an access token",
)
def login(payload: LoginRequest, auth: AuthServiceDep, audit: AuditServiceDep):
    user = auth.authenticate(payload.email, payload.password)
    audit.record(
        action="auth.login", resource_type="user", resource_id=user.id, actor=user
    )
    return TokenOut(**auth.issue_token(user), user=_user_out(user))


@router.get("/auth/me", response_model=UserOut, summary="The signed-in account")
def me(user: CurrentUser):
    return _user_out(user)


@router.get("/auth/config", summary="Whether this deployment requires sign-in")
def auth_config():
    settings = get_settings()
    return {
        "auth_enabled": settings.auth_enabled,
        "roles": [role.value for role in Role],
        "permissions": [permission.value for permission in Permission],
    }


@router.post(
    "/auth/password", response_model=UserOut, summary="Change your own password"
)
def change_password(
    payload: PasswordChange,
    user: CurrentUser,
    auth: AuthServiceDep,
    audit: AuditServiceDep,
):
    updated = auth.change_own_password(
        user, payload.current_password, payload.new_password
    )
    audit.record(
        action="auth.password_change", resource_type="user",
        resource_id=updated.id, actor=updated,
    )
    return _user_out(updated)


# --------------------------------------------------------------------------
# user administration
# --------------------------------------------------------------------------
@router.get("/users", response_model=list[UserOut], summary="All accounts")
def list_users(auth: AuthServiceDep, _: AdminUser):
    return [_user_out(user) for user in auth.list_users()]


@router.post(
    "/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
def create_user(
    payload: UserCreate, auth: AuthServiceDep, audit: AuditServiceDep, actor: AdminUser
):
    created = auth.create_user(
        email=payload.email,
        password=payload.password,
        role=payload.role,
        display_name=payload.display_name,
    )
    audit.record(
        action="user.create", resource_type="user", resource_id=created.id,
        actor=actor, detail={"email": created.email, "role": created.role.value},
    )
    return _user_out(created)


@router.patch("/users/{user_id}", response_model=UserOut, summary="Update an account")
def update_user(
    user_id: str,
    payload: UserUpdate,
    auth: AuthServiceDep,
    audit: AuditServiceDep,
    actor: AdminUser,
):
    changes = payload.model_dump(exclude_unset=True)
    updated = auth.update_user(user_id, changes)
    audit.record(
        action="user.update", resource_type="user", resource_id=user_id, actor=actor,
        detail={k: v for k, v in changes.items() if k != "password"},
    )
    return _user_out(updated)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an account",
)
def delete_user(
    user_id: str, auth: AuthServiceDep, audit: AuditServiceDep, actor: AdminUser
) -> None:
    auth.delete_user(user_id, acting_user=actor)
    audit.record(
        action="user.delete", resource_type="user", resource_id=user_id, actor=actor
    )


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role.value,
        is_active=user.is_active,
        permissions=sorted(p.value for p in user.permissions),
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


TokenOut.model_rebuild()
