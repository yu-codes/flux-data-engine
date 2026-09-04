"""Project tables."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.projects import Project


class ProjectRow(Base):
    __tablename__ = "projects"
    #  Unique within a workspace rather than installation-wide: two teams may
    #  both have a "Demo", and the workspace is what keeps them apart.
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_project_name"),
        UniqueConstraint("workspace_id", "slug", name="uq_project_slug"),
        UniqueConstraint("workspace_id", "directory", name="uq_project_directory"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    #  Two projects cannot share a directory, or one would silently read the
    #  other's files.
    directory: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def project_to_entity(row: ProjectRow) -> Project:
    return Project(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        slug=row.slug,
        directory=row.directory,
        description=row.description or "",
        is_default=bool(row.is_default),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def project_to_row(entity: Project, row: ProjectRow | None = None) -> ProjectRow:
    row = row or ProjectRow(id=entity.id, created_at=entity.created_at)
    row.workspace_id = entity.workspace_id
    row.name = entity.name
    row.slug = entity.slug
    row.directory = entity.directory
    row.description = entity.description
    row.is_default = entity.is_default
    row.created_by = entity.created_by
    row.updated_at = entity.updated_at
    return row
