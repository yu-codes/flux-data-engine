"""Report tables. Same table names, new home."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import Report, ReportSection, ReportStatus


class ReportRow(Base):
    __tablename__ = "reports"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    last_export_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_export_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_exported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

def report_to_entity(row: ReportRow) -> Report:
    return Report(
        id=row.id,
        name=row.name,
        description=row.description or "",
        sections=[ReportSection.from_dict(s) for s in (row.sections or [])],
        status=ReportStatus(row.status),
        tags=list(row.tags or []),
        last_export_uri=row.last_export_uri,
        last_export_format=row.last_export_format,
        last_exported_at=row.last_exported_at,
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def report_to_row(entity: Report, row: ReportRow | None = None) -> ReportRow:
    row = row or ReportRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.sections = [s.to_dict() for s in entity.sections]
    row.status = entity.status.value
    row.tags = entity.tags
    row.last_export_uri = entity.last_export_uri
    row.last_export_format = entity.last_export_format
    row.last_exported_at = entity.last_exported_at
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row
