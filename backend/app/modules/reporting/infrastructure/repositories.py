"""SQL persistence for reports."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Report
from . import orm


class SqlReportRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, report: Report) -> Report:
        self.session.add(self._stamp(orm.report_to_row(report)))
        self.session.flush()
        return report

    def get(self, report_id: str) -> Report | None:
        row = self._fetch(orm.ReportRow, report_id)
        return orm.report_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Report | None:
        row = self.session.scalar(
            self._scoped(select(orm.ReportRow), orm.ReportRow)
            .where(orm.ReportRow.name == name)
        )
        return orm.report_to_entity(row) if row else None

    def list(self) -> list[Report]:
        rows = self.session.scalars(
            self._scoped(select(orm.ReportRow), orm.ReportRow)
            .order_by(orm.ReportRow.created_at.desc())
        ).all()
        return [orm.report_to_entity(r) for r in rows]

    def update(self, report: Report) -> Report:
        row = self._fetch(orm.ReportRow, report.id)
        orm.report_to_row(report, row)
        self.session.flush()
        return report

    def delete(self, report_id: str) -> None:
        row = self._fetch(orm.ReportRow, report_id)
        if row:
            self.session.delete(row)
            self.session.flush()
