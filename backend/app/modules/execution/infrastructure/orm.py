"""SQLAlchemy mapping for the execution module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.model.domain.plugin import ExecutionKind
from app.shared.ids import as_utc, utcnow

from ..domain.entities import Execution, ExecutionStatus, RunnableKind


class ExecutionRow(Base):
    __tablename__ = "executions"

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
    #  Nullable: an execution may run an inline definition - a pipeline step,
    #  say - which has no row in the model library to point at.
    target_type: Mapped[str] = mapped_column(String(16), default="model", index=True)
    #  No foreign key, and there cannot be one: what a target points at depends
    #  on target_type, and no column can reference two tables. It carried one
    #  to models while a model was the only thing that could be executed, and
    #  the first pipeline execution on PostgreSQL was rejected by it. SQLite
    #  does not enforce foreign keys unless told to, which is why the whole
    #  test suite had nothing to say about it - the suite now tells it to.
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    model_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime: Mapped[str] = mapped_column(String(32), default="python")
    status: Mapped[str] = mapped_column(String(16), default=ExecutionStatus.PENDING.value)
    result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    produced_model_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    logs: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def to_entity(row: ExecutionRow) -> Execution:
    return Execution(
        id=row.id,
        target_id=row.target_id,
        target_type=RunnableKind(row.target_type or "model"),
        model_version_id=row.model_version_id,
        kind=ExecutionKind(row.kind),
        dataset_version_id=row.dataset_version_id,
        input_payload=row.input_payload or {},
        parameters=row.parameters or {},
        context=row.context or {},
        runtime=row.runtime,
        status=ExecutionStatus(row.status),
        result_id=row.result_id,
        produced_model_version_id=row.produced_model_version_id,
        experiment_id=row.experiment_id,
        project_id=row.project_id,
        logs=list(row.logs or []),
        metrics=row.metrics or {},
        lineage=row.lineage or {},
        error=row.error,
        definition_snapshot=row.definition_snapshot or {},
        cancel_requested=bool(row.cancel_requested),
        attempts=int(row.attempts or 0),
        heartbeat_at=as_utc(row.heartbeat_at),
        started_at=as_utc(row.started_at),
        finished_at=as_utc(row.finished_at),
        created_at=row.created_at,
    )


def to_row(entity: Execution, row: ExecutionRow | None = None) -> ExecutionRow:
    row = row or ExecutionRow(id=entity.id)
    row.target_id = entity.target_id
    row.target_type = entity.target_type.value
    row.model_version_id = entity.model_version_id
    row.kind = entity.kind.value
    row.dataset_version_id = entity.dataset_version_id
    row.input_payload = entity.input_payload
    row.parameters = entity.parameters
    row.context = entity.context
    row.runtime = entity.runtime
    row.status = entity.status.value
    row.result_id = entity.result_id
    row.produced_model_version_id = entity.produced_model_version_id
    row.experiment_id = entity.experiment_id
    row.logs = entity.logs
    row.metrics = entity.metrics
    row.lineage = entity.lineage
    row.error = entity.error
    row.definition_snapshot = entity.definition_snapshot
    row.cancel_requested = entity.cancel_requested
    row.attempts = entity.attempts
    row.heartbeat_at = entity.heartbeat_at
    row.started_at = entity.started_at
    row.finished_at = entity.finished_at
    row.created_at = entity.created_at
    row.project_id = entity.project_id
    return row
