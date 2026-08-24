"""Authentication and authorisation."""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.shared.errors import ConflictError, FluxError, NotFoundError, ValidationError
from app.shared.ids import utcnow
from app.shared.tokens import decode_token, encode_token, hash_password, verify_password

from ..domain.entities import Permission, Role, User
from ..domain.ports import UserRepository

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8


class AuthenticationError(FluxError):
    status_code = 401
    code = "unauthenticated"


class AuthorizationError(FluxError):
    status_code = 403
    code = "forbidden"


class AuthService:
    def __init__(self, users: UserRepository, settings: Settings):
        self.users = users
        self.settings = settings

    # -- accounts ----------------------------------------------------------
    def create_user(
        self,
        *,
        email: str,
        password: str,
        role: str = Role.VIEWER.value,
        display_name: str = "",
    ) -> User:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValidationError("a valid email address is required")
        if self.users.get_by_email(email):
            raise ConflictError(f"a user with the email '{email}' already exists")
        self._check_password(password)
        return self.users.add(
            User(
                email=email,
                password_hash=hash_password(password),
                role=Role(role),
                display_name=display_name,
            )
        )

    def get_user(self, user_id: str) -> User:
        user = self.users.get(user_id)
        if not user:
            raise NotFoundError(f"user '{user_id}' not found")
        return user

    def list_users(self) -> list[User]:
        return self.users.list()

    def update_user(self, user_id: str, changes: dict) -> User:
        user = self.get_user(user_id)
        if changes.get("display_name") is not None:
            user.display_name = changes["display_name"]
        if changes.get("role"):
            user.role = Role(changes["role"])
        if changes.get("is_active") is not None:
            user.is_active = bool(changes["is_active"])
        if changes.get("password"):
            self._check_password(changes["password"])
            user.password_hash = hash_password(changes["password"])
        user.updated_at = utcnow()
        return self.users.update(user)

    def change_own_password(self, user: User, current: str, replacement: str) -> User:
        if not verify_password(current, user.password_hash):
            raise AuthenticationError("the current password is incorrect")
        self._check_password(replacement)
        user.password_hash = hash_password(replacement)
        user.updated_at = utcnow()
        return self.users.update(user)

    def delete_user(self, user_id: str, *, acting_user: User | None = None) -> None:
        user = self.get_user(user_id)
        if acting_user and acting_user.id == user.id:
            raise ValidationError("you cannot delete your own account")
        if user.role is Role.ADMIN and self._admin_count() <= 1:
            raise ValidationError("the last administrator cannot be removed")
        self.users.delete(user.id)

    # -- sign in -----------------------------------------------------------
    def authenticate(self, email: str, password: str) -> User:
        user = self.users.get_by_email(email.strip().lower())
        #  Hash regardless of whether the account exists so a missing account
        #  and a wrong password take the same time to answer.
        reference = user.password_hash if user else _DUMMY_HASH
        matched = verify_password(password, reference)
        if not user or not matched:
            raise AuthenticationError("incorrect email or password")
        if not user.is_active:
            raise AuthenticationError("this account is disabled")
        user.last_login_at = utcnow()
        return self.users.update(user)

    def issue_token(self, user: User) -> dict:
        expires_in = self.settings.access_token_minutes * 60
        token = encode_token(
            {"sub": user.id, "email": user.email, "role": user.role.value},
            secret=self.settings.secret_key,
            expires_in=expires_in,
        )
        return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}

    def user_from_token(self, token: str) -> User:
        claims = decode_token(token, secret=self.settings.secret_key)
        user = self.users.get(str(claims.get("sub", "")))
        if not user or not user.is_active:
            raise AuthenticationError("this account is no longer active")
        return user

    # -- authorisation -----------------------------------------------------
    @staticmethod
    def authorize(user: User, permission: Permission) -> None:
        if not user.may(permission):
            raise AuthorizationError(
                f"your role ({user.role.value}) does not allow {permission.value}",
                details={"required": permission.value, "role": user.role.value},
            )

    # -- bootstrap ---------------------------------------------------------
    def ensure_bootstrap_admin(self) -> User | None:
        """Create the first administrator on an empty installation."""
        if self.users.count():
            return None
        admin = self.create_user(
            email=self.settings.bootstrap_admin_email,
            password=self.settings.bootstrap_admin_password,
            role=Role.ADMIN.value,
            display_name="Administrator",
        )
        logger.warning(
            "created the bootstrap administrator '%s' - change its password",
            admin.email,
        )
        return admin

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _check_password(password: str) -> None:
        if not password or len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"a password must be at least {MIN_PASSWORD_LENGTH} characters"
            )

    def _admin_count(self) -> int:
        return sum(1 for user in self.users.list() if user.role is Role.ADMIN)


#  A real hash of a value nobody can supply, used to equalise timing.
_DUMMY_HASH = hash_password("flux-timing-equaliser-placeholder")
