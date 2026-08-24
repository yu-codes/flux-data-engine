"""SQLAlchemy repository for executions."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.util import identity_key

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Execution, ExecutionStatus
from . import orm


class SqlExecutionRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, execution: Execution) -> Execution:
        self.session.add(self._stamp(orm.to_row(execution)))
        self.session.flush()
        return execution

    def get(self, execution_id: str) -> Execution | None:
        row = self._fetch(orm.ExecutionRow, execution_id)
        return orm.to_entity(row) if row else None

    def update(self, execution: Execution) -> Execution:
        row = self._fetch(orm.ExecutionRow, execution.id)
        orm.to_row(execution, row)
        self.session.flush()
        return execution

    def claim(self, execution_id: str) -> Execution | None:
        """Move PENDING -> RUNNING in one statement, or answer None.

        The `where status = 'pending'` is the whole point: the database decides
        the winner, and the loser learns it lost from `rowcount` rather than
        from a second read that may already be stale.
        """
        now = utcnow()
        result = self.session.execute(
            update(orm.ExecutionRow)
            .where(
                orm.ExecutionRow.id == execution_id,
                orm.ExecutionRow.status == ExecutionStatus.PENDING.value,
            )
            .values(
                status=ExecutionStatus.RUNNING.value,
                started_at=now,
                heartbeat_at=now,
                attempts=orm.ExecutionRow.attempts + 1,
            )
        )
        if not result.rowcount:
            return None
        #  The UPDATE went through Core, so an ORM object already loaded for
        #  this row still holds the old status. Expire that one row rather
        #  than the whole session: the caller is usually part-way through a
        #  unit of work with other rows loaded on purpose.
        loaded = self.session.identity_map.get(
            identity_key(orm.ExecutionRow, execution_id)
        )
        if loaded is not None:
            self.session.expire(loaded)
        return self.get(execution_id)

    def list(
        self,
        *,
        model_id: str | None = None,
        target_id: str | None = None,
        target_type: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        experiment_id: str | None = None,
        dataset_version_id: str | None = None,
        limit: int = 100,
    ) -> list[Execution]:
        stmt = self._scoped(select(orm.ExecutionRow), orm.ExecutionRow)
        if model_id:
            #  "Which model" is still the common question, and it means a
            #  target of that id which is a model - not a pipeline that
            #  happens to share one.
            stmt = stmt.where(
                orm.ExecutionRow.target_id == model_id,
                orm.ExecutionRow.target_type == "model",
            )
        if target_id:
            stmt = stmt.where(orm.ExecutionRow.target_id == target_id)
        if target_type:
            stmt = stmt.where(orm.ExecutionRow.target_type == target_type)
        if kind:
            stmt = stmt.where(orm.ExecutionRow.kind == kind)
        if status:
            stmt = stmt.where(orm.ExecutionRow.status == status)
        if experiment_id:
            stmt = stmt.where(orm.ExecutionRow.experiment_id == experiment_id)
        if dataset_version_id:
            stmt = stmt.where(
                orm.ExecutionRow.dataset_version_id == dataset_version_id
            )
        rows = self.session.scalars(
            stmt.order_by(orm.ExecutionRow.created_at.desc()).limit(limit)
        ).all()
        return [orm.to_entity(r) for r in rows]

    def delete(self, execution_id: str) -> None:
        row = self._fetch(orm.ExecutionRow, execution_id)
        if row:
            self.session.delete(row)
            self.session.flush()
