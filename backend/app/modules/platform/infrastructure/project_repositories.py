"""SQL persistence for projects.

Scoped to a workspace but not to a project, which would be circular: this is
the table that decides what a project scope means.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.scoping import WorkspaceScope

from ..domain.projects import Project
from . import project_orm as orm


class SqlProjectRepository:
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        self.scope = scope or WorkspaceScope.unscoped()

    def _scoped(self, stmt):
        if self.scope.is_scoped:
            return stmt.where(orm.ProjectRow.workspace_id == self.scope.workspace_id)
        return stmt

    def add(self, project: Project) -> Project:
        if project.workspace_id is None:
            project.workspace_id = self.scope.workspace_id
        self.session.add(orm.project_to_row(project))
        self.session.flush()
        return project

    def get(self, project_id: str) -> Project | None:
        row = self.session.get(orm.ProjectRow, project_id)
        if row is None:
            return None
        if self.scope.is_scoped and row.workspace_id != self.scope.workspace_id:
            return None
        return orm.project_to_entity(row)

    def get_by_slug(self, slug: str) -> Project | None:
        row = self.session.scalar(
            self._scoped(select(orm.ProjectRow).where(orm.ProjectRow.slug == slug))
        )
        return orm.project_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Project | None:
        row = self.session.scalar(
            self._scoped(select(orm.ProjectRow).where(orm.ProjectRow.name == name))
        )
        return orm.project_to_entity(row) if row else None

    def get_default(self) -> Project | None:
        row = self.session.scalar(
            self._scoped(
                select(orm.ProjectRow).where(orm.ProjectRow.is_default.is_(True))
            )
        )
        return orm.project_to_entity(row) if row else None

    def list(self) -> list[Project]:
        stmt = self._scoped(select(orm.ProjectRow)).order_by(
            orm.ProjectRow.is_default.desc(), orm.ProjectRow.name
        )
        return [orm.project_to_entity(row) for row in self.session.scalars(stmt)]

    def update(self, project: Project) -> Project:
        row = self.session.get(orm.ProjectRow, project.id)
        if row is None:
            return self.add(project)
        orm.project_to_row(project, row)
        self.session.flush()
        return orm.project_to_entity(row)

    def delete(self, project_id: str) -> None:
        row = self.session.get(orm.ProjectRow, project_id)
        if row is not None:
            self.session.delete(row)
            self.session.flush()
