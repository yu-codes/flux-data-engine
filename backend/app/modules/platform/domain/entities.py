"""Platform domain: identity, authorisation, audit and scheduling.

These are the cross-cutting concerns every other module leans on but none of
them owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.ids import new_id, utcnow


# --------------------------------------------------------------------------
# identity and authorisation
# --------------------------------------------------------------------------
class Permission(str, Enum):
    """What an action needs. Deliberately coarse: one per domain and verb."""

    DATA_READ = "data:read"
    DATA_WRITE = "data:write"
    #  Registering a source that makes the server connect somewhere - a
    #  database URL, an HTTP endpoint. Held apart from DATA_WRITE because it is
    #  a different kind of power: uploading a CSV adds data, while pointing the
    #  platform at a host makes it a client on a network the user may not be on.
    DATA_CONNECT = "data:connect"
    ANALYSIS_READ = "analysis:read"
    ANALYSIS_WRITE = "analysis:write"
    MODEL_READ = "model:read"
    MODEL_WRITE = "model:write"
    EXECUTION_READ = "execution:read"
    EXECUTION_RUN = "execution:run"
    RESULT_READ = "result:read"
    RESULT_WRITE = "result:write"
    APPLICATION_READ = "application:read"
    APPLICATION_WRITE = "application:write"
    PLATFORM_READ = "platform:read"
    PLATFORM_ADMIN = "platform:admin"


class Role(str, Enum):
    """Three roles cover the realistic separation of duties here."""

    ADMIN = "admin"        # everything, including users and settings
    EDITOR = "editor"      # build and run, but no user administration
    VIEWER = "viewer"      # read-only


_READ_PERMISSIONS = (
    Permission.DATA_READ,
    Permission.ANALYSIS_READ,
    Permission.MODEL_READ,
    Permission.EXECUTION_READ,
    Permission.RESULT_READ,
    Permission.APPLICATION_READ,
    Permission.PLATFORM_READ,
)

_WRITE_PERMISSIONS = (
    Permission.DATA_WRITE,
    Permission.ANALYSIS_WRITE,
    Permission.MODEL_WRITE,
    Permission.EXECUTION_RUN,
    Permission.RESULT_WRITE,
    Permission.APPLICATION_WRITE,
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: frozenset(_READ_PERMISSIONS),
    Role.EDITOR: frozenset(_READ_PERMISSIONS + _WRITE_PERMISSIONS),
    Role.ADMIN: frozenset(Permission),
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS[role]


@dataclass
class User:
    email: str
    password_hash: str
    role: Role = Role.VIEWER
    display_name: str = ""
    is_active: bool = True
    last_login_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("usr"))
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        self.email = self.email.strip().lower()
        if not self.display_name:
            self.display_name = self.email.split("@")[0]

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.role)

    def may(self, permission: Permission) -> bool:
        return self.is_active and permission in self.permissions


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
@dataclass
class AuditEntry:
    """One recorded change. Append-only; nothing rewrites these."""

    action: str                       # e.g. "model.create"
    resource_type: str                # e.g. "model"
    resource_id: str | None = None
    actor_id: str | None = None
    actor_email: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    outcome: str = "succeeded"        # succeeded | failed
    id: str = field(default_factory=lambda: new_id("aud"))
    created_at: datetime = field(default_factory=utcnow)
