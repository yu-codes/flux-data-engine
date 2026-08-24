"""SQLAlchemy repository for results."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Result
from . import orm


class SqlResultRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, result: Result) -> Result:
        self.session.add(self._stamp(orm.to_row(result)))
        self.session.flush()
        return result

    def get(self, result_id: str) -> Result | None:
        row = self._fetch(orm.ResultRow, result_id)
        return orm.to_entity(row) if row else None

    def get_by_execution(self, execution_id: str) -> Result | None:
        row = self.session.scalar(
            self._scoped(select(orm.ResultRow), orm.ResultRow)
            .where(orm.ResultRow.execution_id == execution_id)
        )
        return orm.to_entity(row) if row else None

    def update(self, result: Result) -> Result:
        row = self._fetch(orm.ResultRow, result.id)
        orm.to_row(result, row)
        self.session.flush()
        return result

    def list(self, *, kind: str | None = None, limit: int = 100) -> list[Result]:
        stmt = self._scoped(select(orm.ResultRow), orm.ResultRow)
        if kind:
            stmt = stmt.where(orm.ResultRow.kind == kind)
        rows = self.session.scalars(
            stmt.order_by(orm.ResultRow.created_at.desc()).limit(limit)
        ).all()
        return [orm.to_entity(r) for r in rows]

    def delete(self, result_id: str) -> None:
        row = self._fetch(orm.ResultRow, result_id)
        if row:
            self.session.delete(row)
            self.session.flush()
