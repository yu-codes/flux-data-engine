"""Job table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import as_utc, utcnow

from ..domain.entities import Job, JobStatus


class JobRow(Base):
    __tablename__ = "jobs"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), default=JobStatus.PENDING.value, index=True
    )
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    requested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


def to_entity(row: JobRow) -> Job:
    return Job(
        id=row.id,
        kind=row.kind,
        target_id=row.target_id,
        parameters=row.parameters or {},
        status=JobStatus(row.status),
        outcome=row.outcome or {},
        error=row.error,
        attempts=int(row.attempts or 0),
        cancel_requested=bool(row.cancel_requested),
        heartbeat_at=as_utc(row.heartbeat_at),
        requested_by=row.requested_by,
        started_at=as_utc(row.started_at),
        finished_at=as_utc(row.finished_at),
        created_at=as_utc(row.created_at),
        updated_at=as_utc(row.updated_at),
    )


def to_row(entity: Job, row: JobRow | None = None) -> JobRow:
    row = row or JobRow(id=entity.id, created_at=entity.created_at)
    row.kind = entity.kind
    row.target_id = entity.target_id
    row.parameters = entity.parameters
    row.status = entity.status.value
    row.outcome = entity.outcome
    row.error = entity.error
    row.attempts = entity.attempts
    row.cancel_requested = entity.cancel_requested
    row.heartbeat_at = entity.heartbeat_at
    row.requested_by = entity.requested_by
    row.started_at = entity.started_at
    row.finished_at = entity.finished_at
    row.updated_at = entity.updated_at
    return row
