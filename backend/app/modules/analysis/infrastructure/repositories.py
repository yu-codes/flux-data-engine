"""SQLAlchemy repositories for the analysis module."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Dashboard, Visualization
from . import orm


class SqlVisualizationRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, visualization: Visualization) -> Visualization:
        self.session.add(self._stamp(orm.visualization_to_row(visualization)))
        self.session.flush()
        return visualization

    def get(self, visualization_id: str) -> Visualization | None:
        row = self._fetch(orm.VisualizationRow, visualization_id)
        return orm.visualization_to_entity(row) if row else None

    def list(self) -> list[Visualization]:
        rows = self.session.scalars(
            self._scoped(select(orm.VisualizationRow), orm.VisualizationRow)
            .order_by(orm.VisualizationRow.created_at.desc())
        ).all()
        return [orm.visualization_to_entity(r) for r in rows]

    def update(self, visualization: Visualization) -> Visualization:
        row = self._fetch(orm.VisualizationRow, visualization.id)
        visualization.updated_at = utcnow()
        orm.visualization_to_row(visualization, row)
        self.session.flush()
        return visualization

    def delete(self, visualization_id: str) -> None:
        row = self._fetch(orm.VisualizationRow, visualization_id)
        if row:
            self.session.delete(row)
            self.session.flush()


class SqlDashboardRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, dashboard: Dashboard) -> Dashboard:
        self.session.add(self._stamp(orm.dashboard_to_row(dashboard)))
        self.session.flush()
        return dashboard

    def get(self, dashboard_id: str) -> Dashboard | None:
        row = self._fetch(orm.DashboardRow, dashboard_id)
        return orm.dashboard_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Dashboard | None:
        row = self.session.scalar(
            self._scoped(select(orm.DashboardRow), orm.DashboardRow)
            .where(orm.DashboardRow.name == name)
        )
        return orm.dashboard_to_entity(row) if row else None

    def list(self) -> list[Dashboard]:
        rows = self.session.scalars(
            self._scoped(select(orm.DashboardRow), orm.DashboardRow)
            .order_by(orm.DashboardRow.created_at.desc())
        ).all()
        return [orm.dashboard_to_entity(r) for r in rows]

    def update(self, dashboard: Dashboard) -> Dashboard:
        row = self._fetch(orm.DashboardRow, dashboard.id)
        dashboard.updated_at = utcnow()
        orm.dashboard_to_row(dashboard, row)
        self.session.flush()
        return dashboard

    def delete(self, dashboard_id: str) -> None:
        row = self._fetch(orm.DashboardRow, dashboard_id)
        if row:
            self.session.delete(row)
            self.session.flush()
