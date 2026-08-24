"""SQLAlchemy mapping for applications."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import (
    Application,
    ApplicationKind,
    ApplicationStatus,
    Visibility,
)


class ApplicationRow(Base):
    __tablename__ = "applications"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), default=ApplicationKind.COMPOSED.value)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=ApplicationStatus.DRAFT.value)
    model_ids: Mapped[list] = mapped_column(JSON, default=list)
    dataset_ids: Mapped[list] = mapped_column(JSON, default=list)
    dashboard_ids: Mapped[list] = mapped_column(JSON, default=list)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    entrypoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    visibility: Mapped[str] = mapped_column(String(16), default="workspace")
    #  Indexed because the public route looks an application up by it, and
    #  unique because two applications answering to one link would be a bug
    #  nobody could see.
    share_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    shared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)



def application_to_entity(row: ApplicationRow) -> Application:
    return Application(
        id=row.id,
        name=row.name,
        slug=row.slug,
        kind=ApplicationKind(row.kind),
        description=row.description or "",
        status=ApplicationStatus(row.status),
        model_ids=list(row.model_ids or []),
        dataset_ids=list(row.dataset_ids or []),
        dashboard_ids=list(row.dashboard_ids or []),
        configuration=row.configuration or {},
        entrypoint=row.entrypoint,
        visibility=Visibility(row.visibility or "workspace"),
        share_token=row.share_token,
        shared_at=row.shared_at,
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def application_to_row(
    entity: Application, row: ApplicationRow | None = None
) -> ApplicationRow:
    row = row or ApplicationRow(id=entity.id)
    row.name = entity.name
    row.slug = entity.slug
    row.kind = entity.kind.value
    row.description = entity.description
    row.status = entity.status.value
    row.model_ids = entity.model_ids
    row.dataset_ids = entity.dataset_ids
    row.dashboard_ids = entity.dashboard_ids
    row.configuration = entity.configuration
    row.entrypoint = entity.entrypoint
    row.visibility = entity.visibility.value
    row.share_token = entity.share_token
    row.shared_at = entity.shared_at
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row



