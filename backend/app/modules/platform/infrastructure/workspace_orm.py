"""Workspace tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.workspaces import Workspace, WorkspaceMembership, WorkspaceRole


class WorkspaceRow(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #  A workspace name *is* installation-wide: it is the namespace, so two of
    #  them sharing a name would defeat the point.
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkspaceMemberRow(Base):
    __tablename__ = "workspace_members"
    #  One role per person per workspace: two rows would mean two answers to
    #  the only question this table exists to answer.
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_member"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def workspace_to_entity(row: WorkspaceRow) -> Workspace:
    return Workspace(
        id=row.id,
        name=row.name,
        slug=row.slug,
        description=row.description or "",
        is_default=bool(row.is_default),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def workspace_to_row(entity: Workspace, row: WorkspaceRow | None = None) -> WorkspaceRow:
    row = row or WorkspaceRow(id=entity.id, created_at=entity.created_at)
    row.name = entity.name
    row.slug = entity.slug
    row.description = entity.description
    row.is_default = entity.is_default
    row.created_by = entity.created_by
    row.updated_at = entity.updated_at
    return row


def member_to_entity(row: WorkspaceMemberRow) -> WorkspaceMembership:
    return WorkspaceMembership(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        role=WorkspaceRole(row.role),
        created_at=row.created_at,
    )


def member_to_row(entity: WorkspaceMembership) -> WorkspaceMemberRow:
    return WorkspaceMemberRow(
        id=entity.id,
        workspace_id=entity.workspace_id,
        user_id=entity.user_id,
        role=entity.role.value,
        created_at=entity.created_at,
    )
