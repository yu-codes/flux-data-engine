"""SQLAlchemy repositories for the platform module."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import AuditEntry, User
from . import orm


class SqlUserRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, user: User) -> User:
        self.session.add(self._stamp(orm.user_to_row(user)))
        self.session.flush()
        return user

    def get(self, user_id: str) -> User | None:
        row = self._fetch(orm.UserRow, user_id)
        return orm.user_to_entity(row) if row else None

    def get_by_email(self, email: str) -> User | None:
        row = self.session.scalar(
            self._scoped(select(orm.UserRow), orm.UserRow)
            .where(orm.UserRow.email == email.strip().lower())
        )
        return orm.user_to_entity(row) if row else None

    def list(self) -> list[User]:
        rows = self.session.scalars(
            self._scoped(select(orm.UserRow), orm.UserRow)
            .order_by(orm.UserRow.created_at.asc())
        ).all()
        return [orm.user_to_entity(r) for r in rows]

    def update(self, user: User) -> User:
        row = self._fetch(orm.UserRow, user.id)
        orm.user_to_row(user, row)
        self.session.flush()
        return user

    def delete(self, user_id: str) -> None:
        row = self._fetch(orm.UserRow, user_id)
        if row:
            self.session.delete(row)
            self.session.flush()

    def count(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(orm.UserRow)) or 0)


class SqlAuditRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, entry: AuditEntry) -> AuditEntry:
        self.session.add(self._stamp(orm.audit_to_row(entry)))
        self.session.flush()
        return entry

    def list(
        self,
        *,
        actor_id: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        stmt = self._scoped(select(orm.AuditRow), orm.AuditRow)
        if actor_id:
            stmt = stmt.where(orm.AuditRow.actor_id == actor_id)
        if resource_type:
            stmt = stmt.where(orm.AuditRow.resource_type == resource_type)
        if action:
            stmt = stmt.where(orm.AuditRow.action == action)
        rows = self.session.scalars(
            stmt.order_by(orm.AuditRow.created_at.desc()).limit(limit)
        ).all()
        return [orm.audit_to_entity(r) for r in rows]
