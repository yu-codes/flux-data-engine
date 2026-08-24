"""SQLAlchemy mapping for the platform module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import AuditEntry, Role, User


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=Role.VIEWER.value)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditRow(Base):
    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(16), default="succeeded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

def user_to_entity(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        password_hash=row.password_hash,
        role=Role(row.role),
        display_name=row.display_name or "",
        is_active=row.is_active,
        last_login_at=row.last_login_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def user_to_row(entity: User, row: UserRow | None = None) -> UserRow:
    row = row or UserRow(id=entity.id)
    row.email = entity.email
    row.password_hash = entity.password_hash
    row.role = entity.role.value
    row.display_name = entity.display_name
    row.is_active = entity.is_active
    row.last_login_at = entity.last_login_at
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    return row


def audit_to_entity(row: AuditRow) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        actor_id=row.actor_id,
        actor_email=row.actor_email,
        detail=row.detail or {},
        outcome=row.outcome,
        created_at=row.created_at,
    )


def audit_to_row(entity: AuditEntry) -> AuditRow:
    return AuditRow(
        id=entity.id,
        action=entity.action,
        resource_type=entity.resource_type,
        resource_id=entity.resource_id,
        actor_id=entity.actor_id,
        actor_email=entity.actor_email,
        detail=entity.detail,
        outcome=entity.outcome,
        created_at=entity.created_at,
    )
