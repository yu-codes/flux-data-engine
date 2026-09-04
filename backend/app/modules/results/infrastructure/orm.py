"""SQLAlchemy mapping for the results module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import Result, ResultKind


class ResultRow(Base):
    __tablename__ = "results"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #  Which project files this. A filing system rather than a boundary: a
    #  listing filters by it, a lookup by id does not. Null means "not filed",
    #  which shows in every project rather than in none.
    project_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    inline_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artifact_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def to_entity(row: ResultRow) -> Result:
    return Result(
        id=row.id,
        execution_id=row.execution_id,
        kind=ResultKind(row.kind),
        summary=row.summary or {},
        metrics=row.metrics or {},
        inline_payload=row.inline_payload,
        payload_uri=row.payload_uri,
        artifact_uri=row.artifact_uri,
        dataset_id=row.dataset_id,
        dataset_version_id=row.dataset_version_id,
        project_id=row.project_id,
        row_count=row.row_count,
        created_at=row.created_at,
    )


def to_row(entity: Result, row: ResultRow | None = None) -> ResultRow:
    row = row or ResultRow(id=entity.id)
    row.execution_id = entity.execution_id
    row.kind = entity.kind.value
    row.summary = entity.summary
    row.metrics = entity.metrics
    row.inline_payload = entity.inline_payload
    row.payload_uri = entity.payload_uri
    row.artifact_uri = entity.artifact_uri
    row.dataset_id = entity.dataset_id
    row.dataset_version_id = entity.dataset_version_id
    row.row_count = entity.row_count
    row.created_at = entity.created_at
    row.project_id = entity.project_id
    return row
