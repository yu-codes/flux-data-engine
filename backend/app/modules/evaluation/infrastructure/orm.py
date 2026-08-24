"""Experiment and evaluation tables. Same table names, new home."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import Evaluation, Experiment, ExperimentTrial


class ExperimentRow(Base):
    __tablename__ = "experiments"

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
    objective: Mapped[str] = mapped_column(Text, default="")
    primary_direction: Mapped[str] = mapped_column(String(8), default="higher")
    primary_metric: Mapped[str] = mapped_column(String(128), default="")
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trials: Mapped[list] = mapped_column(JSON, default=list)
    execution_ids: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class EvaluationRow(Base):
    __tablename__ = "evaluations"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    target: Mapped[dict] = mapped_column(JSON, default=dict)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# mappers
# --------------------------------------------------------------------------

def experiment_to_entity(row: ExperimentRow) -> Experiment:
    return Experiment(
        id=row.id,
        name=row.name,
        description=row.description or "",
        objective=row.objective or "",
        primary_direction=row.primary_direction or "higher",
        primary_metric=row.primary_metric or "",
        dataset_version_id=row.dataset_version_id,
        trials=[ExperimentTrial.from_dict(t) for t in (row.trials or [])],
        execution_ids=list(row.execution_ids or []),
        metadata=row.meta or {},
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )

def experiment_to_row(
    entity: Experiment, row: ExperimentRow | None = None
) -> ExperimentRow:
    row = row or ExperimentRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.objective = entity.objective
    row.primary_direction = entity.primary_direction
    row.primary_metric = entity.primary_metric
    row.dataset_version_id = entity.dataset_version_id
    row.trials = [trial.to_dict() for trial in entity.trials]
    row.execution_ids = entity.execution_ids
    row.meta = entity.metadata
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row

def evaluation_to_entity(row: EvaluationRow) -> Evaluation:
    return Evaluation(
        id=row.id,
        execution_id=row.execution_id,
        model_id=row.model_id,
        experiment_id=row.experiment_id,
        metrics=row.metrics or {},
        target=row.target or {},
        passed=row.passed,
        notes=row.notes or "",
        created_at=row.created_at,
    )

def evaluation_to_row(entity: Evaluation) -> EvaluationRow:
    return EvaluationRow(
        id=entity.id,
        execution_id=entity.execution_id,
        model_id=entity.model_id,
        experiment_id=entity.experiment_id,
        metrics=entity.metrics,
        target=entity.target,
        passed=entity.passed,
        notes=entity.notes,
        created_at=entity.created_at,
    )
