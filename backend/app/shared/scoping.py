"""Which workspace a unit of work belongs to.

Every request happens inside one workspace, and every row a request creates
belongs to it. Putting that in one place rather than in seventeen repositories
is what makes it hard to forget: a repository that stamps and filters through
these helpers is isolated by construction, and one that does not shows up as
an obvious difference.

`unscoped()` exists for the background worker, which by definition works across
workspaces - it pops whatever the queue hands it. That is a real requirement
rather than a loophole, so it has a name and says what it is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class WorkspaceScope:
    """The workspace, and the person, a unit of work is acting for."""

    workspace_id: str | None = None
    user_id: str | None = None

    @classmethod
    def unscoped(cls) -> WorkspaceScope:
        """For the worker: every workspace, because the queue is shared."""
        return cls()

    @property
    def is_scoped(self) -> bool:
        return self.workspace_id is not None


class WorkspaceScoped:
    """Mixin for a repository that keeps to one workspace.

    Three operations, corresponding to the three ways isolation is lost:
    writing without an owner, listing without a filter, and reading a row by an
    id that came from somewhere else.
    """

    session: Session
    scope: WorkspaceScope

    def _stamp(self, row: Any) -> Any:
        """Record which workspace this row belongs to, and who made it."""
        if self.scope.is_scoped and hasattr(row, "workspace_id"):
            row.workspace_id = self.scope.workspace_id
        if hasattr(row, "created_by") and getattr(row, "created_by", None) is None:
            row.created_by = self.scope.user_id
        return row

    def _scoped(self, stmt: Any, row_class: Any) -> Any:
        """Restrict a query to this workspace."""
        if self.scope.is_scoped and hasattr(row_class, "workspace_id"):
            return stmt.where(row_class.workspace_id == self.scope.workspace_id)
        return stmt

    def _fetch(self, row_class: Any, row_id: str) -> Any:
        """Load a row by id, but only if it belongs here.

        Without this check, a workspace is a listing filter rather than a
        boundary: anybody holding an id from elsewhere could read the row.
        """
        #  A caller with no id has nothing to look up. Asking SQLAlchemy
        #  anyway warns about a null primary key on every optional lookup.
        if not row_id:
            return None
        row = self.session.get(row_class, row_id)
        if row is None:
            return None
        if (
            self.scope.is_scoped
            and hasattr(row, "workspace_id")
            and row.workspace_id
            and row.workspace_id != self.scope.workspace_id
        ):
            return None
        return row
