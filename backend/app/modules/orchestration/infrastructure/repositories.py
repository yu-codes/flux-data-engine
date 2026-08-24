"""SQL persistence for pipelines."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import (
    Pipeline,
    PipelineRun,
)
from . import orm


class SqlPipelineRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    # -- pipelines ---------------------------------------------------------
    def add(self, pipeline: Pipeline) -> Pipeline:
        self.session.add(self._stamp(orm.pipeline_to_row(pipeline)))
        self.session.flush()
        return pipeline

    def get(self, pipeline_id: str) -> Pipeline | None:
        row = self._fetch(orm.PipelineRow, pipeline_id)
        return orm.pipeline_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Pipeline | None:
        row = self.session.scalar(
            self._scoped(select(orm.PipelineRow), orm.PipelineRow)
            .where(orm.PipelineRow.name == name)
        )
        return orm.pipeline_to_entity(row) if row else None

    def list(self) -> list[Pipeline]:
        rows = self.session.scalars(
            self._scoped(select(orm.PipelineRow), orm.PipelineRow)
            .order_by(orm.PipelineRow.created_at.desc())
        ).all()
        return [orm.pipeline_to_entity(r) for r in rows]

    def update(self, pipeline: Pipeline) -> Pipeline:
        row = self._fetch(orm.PipelineRow, pipeline.id)
        orm.pipeline_to_row(pipeline, row)
        self.session.flush()
        return pipeline

    def delete(self, pipeline_id: str) -> None:
        row = self._fetch(orm.PipelineRow, pipeline_id)
        if row:
            self.session.delete(row)
            self.session.flush()

    # -- runs --------------------------------------------------------------
    def add_run(self, run: PipelineRun) -> PipelineRun:
        self.session.add(self._stamp(orm.pipeline_run_to_row(run)))
        self.session.flush()
        return run

    def get_run(self, run_id: str) -> PipelineRun | None:
        row = self._fetch(orm.PipelineRunRow, run_id)
        return orm.pipeline_run_to_entity(row) if row else None

    def list_runs(
        self, *, pipeline_id: str | None = None, limit: int = 50
    ) -> list[PipelineRun]:
        stmt = self._scoped(select(orm.PipelineRunRow), orm.PipelineRunRow)
        if pipeline_id:
            stmt = stmt.where(orm.PipelineRunRow.pipeline_id == pipeline_id)
        rows = self.session.scalars(
            stmt.order_by(orm.PipelineRunRow.created_at.desc()).limit(limit)
        ).all()
        return [orm.pipeline_run_to_entity(r) for r in rows]

    def update_run(self, run: PipelineRun) -> PipelineRun:
        row = self._fetch(orm.PipelineRunRow, run.id)
        orm.pipeline_run_to_row(run, row)
        self.session.flush()
        return run
