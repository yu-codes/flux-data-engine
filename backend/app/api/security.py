"""Authentication and authorisation for the API.

Permission is decided by (module, HTTP method) rather than route by route, so a
new endpoint cannot be added without a guard by accident: safe methods need the
module's read permission, unsafe ones need its write permission.

When `FLUX_AUTH_ENABLED=false` the guards resolve to a synthetic administrator.
That keeps local development frictionless without leaving a second, untested
code path in the authorisation logic itself.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import ServicesDep
from app.core.config import get_settings
from app.modules.platform.application.auth import AuthenticationError
from app.modules.platform.domain.entities import Permission, Role, User

#  auto_error=False so a missing header produces our own 401 envelope.
bearer_scheme = HTTPBearer(auto_error=False, description="Bearer access token")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#  The account used when authentication is switched off.
ANONYMOUS_ADMIN = User(
    id="usr_local_admin",
    email="local@flux.local",
    password_hash="",
    role=Role.ADMIN,
    display_name="Local (auth disabled)",
)


def current_user(
    request: Request,
    services: ServicesDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ] = None,
) -> User:
    """Who is asking: a person with a session, or a system with a key."""
    if not get_settings().auth_enabled:
        return ANONYMOUS_ADMIN

    #  A key is a principal in its own right. It stands in as a user so that
    #  every guard, audit entry and permission check downstream works the same
    #  way whether a person or a service is calling - one path, not two.
    key = services.api_keys.resolve(request.headers.get("X-Api-Key") or "")
    if key is not None:
        return _principal_for(key)

    if credentials is None or not credentials.credentials:
        raise AuthenticationError(
            "this endpoint needs an access token or an API key"
        )
    return services.auth.user_from_token(credentials.credentials)


def _principal_for(key) -> User:
    """A key, expressed as the user it acts as.

    Read-only unless the key was issued as writable. A key that could do
    everything its creator can would be a password with extra steps.
    """
    return User(
        id=key.id,
        email=f"{key.hint}@api-key.local",
        password_hash="",
        role=Role.EDITOR if key.can_write else Role.VIEWER,
        display_name=f"API key · {key.name}",
    )


CurrentUser = Annotated[User, Depends(current_user)]


class ModuleGuard:
    """Checks the caller may act on this module with this HTTP method."""

    def __init__(self, read: Permission, write: Permission):
        self.read = read
        self.write = write

    def __call__(self, request: Request, user: CurrentUser) -> User:
        required = self.read if request.method in SAFE_METHODS else self.write
        if get_settings().auth_enabled:
            #  AuthService.authorize is a pure function of user and permission.
            from app.modules.platform.application.auth import AuthService

            AuthService.authorize(user, required)
        request.state.user = user
        request.state.permission = required.value
        return user


def requires(permission: Permission):
    """Dependency for a single endpoint that needs more than its module's default."""

    def _guard(request: Request, user: CurrentUser) -> User:
        if get_settings().auth_enabled:
            from app.modules.platform.application.auth import AuthService

            AuthService.authorize(user, permission)
        request.state.user = user
        return user

    return _guard


#  One guard per module, mirroring the sidebar's grouping.
DATA_GUARD = ModuleGuard(Permission.DATA_READ, Permission.DATA_WRITE)
ANALYSIS_GUARD = ModuleGuard(Permission.ANALYSIS_READ, Permission.ANALYSIS_WRITE)
MODEL_GUARD = ModuleGuard(Permission.MODEL_READ, Permission.MODEL_WRITE)
EXECUTION_GUARD = ModuleGuard(Permission.EXECUTION_READ, Permission.EXECUTION_RUN)
#  Invoking a model computes an answer and records nothing, so it is read
#  permission on both verbs - which is what lets a read-only integration
#  key call a model without also being able to change anything.
SERVING_GUARD = ModuleGuard(Permission.EXECUTION_READ, Permission.EXECUTION_READ)
RESULT_GUARD = ModuleGuard(Permission.RESULT_READ, Permission.RESULT_WRITE)
APPLICATION_GUARD = ModuleGuard(
    Permission.APPLICATION_READ, Permission.APPLICATION_WRITE
)
PLATFORM_GUARD = ModuleGuard(Permission.PLATFORM_READ, Permission.PLATFORM_ADMIN)
#  A built-in application reads as an application and submits executions, so it
#  needs both. Named for the kind of thing it guards, not for any one of them:
#  the core should not know which applications exist.
BUILTIN_APP_GUARD = ModuleGuard(Permission.APPLICATION_READ, Permission.EXECUTION_RUN)

AdminUser = Annotated[User, Depends(requires(Permission.PLATFORM_ADMIN))]

#  Source types where creating the source means the server will connect out.
OUTBOUND_SOURCE_TYPES = frozenset({"database", "rest_api"})


def authorize_source_type(user: User, source_type: str) -> None:
    """Registering an outbound source needs more than an ordinary data write.

    Checked here rather than in the service because it is an authorization
    question - it depends on who is asking, which is not something a data
    service should have to know.
    """
    if source_type not in OUTBOUND_SOURCE_TYPES:
        return
    if not get_settings().auth_enabled:
        return
    from app.modules.platform.application.auth import AuthService

    AuthService.authorize(user, Permission.DATA_CONNECT)
