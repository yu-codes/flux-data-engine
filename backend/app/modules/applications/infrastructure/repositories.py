"""SQLAlchemy repositories for the applications module."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import Application
from . import orm


class SqlApplicationRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, application: Application) -> Application:
        self.session.add(self._stamp(orm.application_to_row(application)))
        self.session.flush()
        return application

    def get(self, application_id: str) -> Application | None:
        row = self._fetch(orm.ApplicationRow, application_id)
        return orm.application_to_entity(row) if row else None

    def get_by_slug(self, slug: str) -> Application | None:
        row = self.session.scalar(
            self._scoped(select(orm.ApplicationRow), orm.ApplicationRow)
            .where(orm.ApplicationRow.slug == slug)
        )
        return orm.application_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Application | None:
        row = self.session.scalar(
            self._scoped(select(orm.ApplicationRow), orm.ApplicationRow)
            .where(orm.ApplicationRow.name == name)
        )
        return orm.application_to_entity(row) if row else None

    def get_by_share_token(self, token: str) -> Application | None:
        """Find an application by its share link.

        Deliberately not workspace-scoped: whoever holds the link is by
        definition outside, with no workspace of their own. The token is the
        entire credential, which is why it is long, random and revocable.
        """
        row = self.session.scalar(
            select(orm.ApplicationRow).where(orm.ApplicationRow.share_token == token)
        )
        return orm.application_to_entity(row) if row else None

    def list(self) -> list[Application]:
        rows = self.session.scalars(
            self._scoped(select(orm.ApplicationRow), orm.ApplicationRow)
            .order_by(orm.ApplicationRow.created_at.desc())
        ).all()
        return [orm.application_to_entity(r) for r in rows]

    def update(self, application: Application) -> Application:
        row = self._fetch(orm.ApplicationRow, application.id)
        application.updated_at = utcnow()
        orm.application_to_row(application, row)
        self.session.flush()
        return application

    def delete(self, application_id: str) -> None:
        row = self._fetch(orm.ApplicationRow, application_id)
        if row:
            self.session.delete(row)
            self.session.flush()


