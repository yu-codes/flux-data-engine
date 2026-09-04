"""Pipeline tables.

The table names are unchanged, so this is a code move and not a data
migration: the same rows are read by the same columns from a new module.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import (
    Pipeline,
    PipelineRun,
    PipelineStatus,
    PipelineStep,
    RunStatus,
    StepRun,
)

# --------------------------------------------------------------------------


class PipelineRow(Base):
    __tablename__ = "pipelines"

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
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    input_dataset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    last_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineRunRow(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pipeline_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    input_dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    definition_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_runs: Mapped[list] = mapped_column(JSON, default=list)
    output_dataset_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def pipeline_to_entity(row: PipelineRow) -> Pipeline:
    return Pipeline(
        id=row.id,
        name=row.name,
        description=row.description or "",
        input_dataset_id=row.input_dataset_id,
        steps=[PipelineStep.from_dict(s) for s in (row.steps or [])],
        status=PipelineStatus(row.status),
        tags=list(row.tags or []),
        last_run_id=row.last_run_id,
        last_run_status=row.last_run_status,
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        updated_at=row.updated_at,
    )


def pipeline_to_row(entity: Pipeline, row: PipelineRow | None = None) -> PipelineRow:
    row = row or PipelineRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.input_dataset_id = entity.input_dataset_id
    row.steps = [s.to_dict() for s in entity.steps]
    row.status = entity.status.value
    row.tags = entity.tags
    row.last_run_id = entity.last_run_id
    row.last_run_status = entity.last_run_status
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    row.project_id = entity.project_id
    return row


def pipeline_run_to_entity(row: PipelineRunRow) -> PipelineRun:
    return PipelineRun(
        id=row.id,
        pipeline_id=row.pipeline_id,
        status=RunStatus(row.status),
        input_dataset_version_id=row.input_dataset_version_id,
        definition_snapshot=row.definition_snapshot or {},
        execution_id=row.execution_id,
        step_runs=[StepRun.from_dict(s) for s in (row.step_runs or [])],
        output_dataset_ids=list(row.output_dataset_ids or []),
        error=row.error,
        triggered_by=row.triggered_by,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
    )


def pipeline_run_to_row(
    entity: PipelineRun, row: PipelineRunRow | None = None
) -> PipelineRunRow:
    row = row or PipelineRunRow(id=entity.id)
    row.pipeline_id = entity.pipeline_id
    row.status = entity.status.value
    row.input_dataset_version_id = entity.input_dataset_version_id
    row.definition_snapshot = entity.definition_snapshot
    row.execution_id = entity.execution_id
    row.step_runs = [s.to_dict() for s in entity.step_runs]
    row.output_dataset_ids = entity.output_dataset_ids
    row.error = entity.error
    row.triggered_by = entity.triggered_by
    row.started_at = entity.started_at
    row.finished_at = entity.finished_at
    row.created_at = entity.created_at
    return row
