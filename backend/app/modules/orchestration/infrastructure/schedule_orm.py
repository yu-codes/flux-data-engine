"""Schedule table. Same table name, new home."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.schedules import Schedule, ScheduleStatus, ScheduleTarget


class ScheduleRow(Base):
    __tablename__ = "schedules"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), default="model")
    kind: Mapped[str] = mapped_column(String(32), default="prediction")
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cron: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=ScheduleStatus.ACTIVE.value)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# mappers
# --------------------------------------------------------------------------


def schedule_to_entity(row: ScheduleRow) -> Schedule:
    return Schedule(
        id=row.id,
        name=row.name,
        description=row.description or "",
        target_id=row.target_id,
        target_type=ScheduleTarget(row.target_type or "model"),
        kind=row.kind,
        interval_seconds=row.interval_seconds,
        cron=row.cron,
        dataset_id=row.dataset_id,
        dataset_version_id=row.dataset_version_id,
        input_payload=row.input_payload or {},
        parameters=row.parameters or {},
        status=ScheduleStatus(row.status),
        last_run_at=row.last_run_at,
        last_execution_id=row.last_execution_id,
        last_status=row.last_status,
        last_error=row.last_error,
        next_run_at=row.next_run_at,
        run_count=row.run_count,
        failure_count=row.failure_count,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def schedule_to_row(entity: Schedule, row: ScheduleRow | None = None) -> ScheduleRow:
    row = row or ScheduleRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.target_id = entity.target_id
    row.target_type = entity.target_type.value
    row.kind = entity.kind
    row.interval_seconds = entity.interval_seconds
    row.cron = entity.cron
    row.dataset_id = entity.dataset_id
    row.dataset_version_id = entity.dataset_version_id
    row.input_payload = entity.input_payload
    row.parameters = entity.parameters
    row.status = entity.status.value
    row.last_run_at = entity.last_run_at
    row.last_execution_id = entity.last_execution_id
    row.last_status = entity.last_status
    row.last_error = entity.last_error
    row.next_run_at = entity.next_run_at
    row.run_count = entity.run_count
    row.failure_count = entity.failure_count
    row.created_by = entity.created_by
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    return row
