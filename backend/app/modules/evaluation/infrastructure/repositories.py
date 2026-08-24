"""SQL persistence for experiments and evaluations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Evaluation, Experiment
from . import orm


class SqlExperimentRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, experiment: Experiment) -> Experiment:
        self.session.add(self._stamp(orm.experiment_to_row(experiment)))
        self.session.flush()
        return experiment

    def get(self, experiment_id: str) -> Experiment | None:
        row = self._fetch(orm.ExperimentRow, experiment_id)
        return orm.experiment_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Experiment | None:
        row = self.session.scalar(
            self._scoped(select(orm.ExperimentRow), orm.ExperimentRow)
            .where(orm.ExperimentRow.name == name)
        )
        return orm.experiment_to_entity(row) if row else None

    def list(self) -> list[Experiment]:
        rows = self.session.scalars(
            self._scoped(select(orm.ExperimentRow), orm.ExperimentRow)
            .order_by(orm.ExperimentRow.created_at.desc())
        ).all()
        return [orm.experiment_to_entity(r) for r in rows]

    def update(self, experiment: Experiment) -> Experiment:
        row = self._fetch(orm.ExperimentRow, experiment.id)
        experiment.updated_at = utcnow()
        orm.experiment_to_row(experiment, row)
        self.session.flush()
        return experiment

    def delete(self, experiment_id: str) -> None:
        row = self._fetch(orm.ExperimentRow, experiment_id)
        if row:
            self.session.delete(row)
            self.session.flush()

class SqlEvaluationRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, evaluation: Evaluation) -> Evaluation:
        self.session.add(self._stamp(orm.evaluation_to_row(evaluation)))
        self.session.flush()
        return evaluation

    def get(self, evaluation_id: str) -> Evaluation | None:
        row = self._fetch(orm.EvaluationRow, evaluation_id)
        return orm.evaluation_to_entity(row) if row else None

    def list(
        self, *, model_id: str | None = None, experiment_id: str | None = None
    ) -> list[Evaluation]:
        stmt = self._scoped(select(orm.EvaluationRow), orm.EvaluationRow)
        if model_id:
            stmt = stmt.where(orm.EvaluationRow.model_id == model_id)
        if experiment_id:
            stmt = stmt.where(orm.EvaluationRow.experiment_id == experiment_id)
        rows = self.session.scalars(
            stmt.order_by(orm.EvaluationRow.created_at.desc())
        ).all()
        return [orm.evaluation_to_entity(r) for r in rows]
