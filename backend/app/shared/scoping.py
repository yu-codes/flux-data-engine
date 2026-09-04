"""Which workspace, and which project, a unit of work belongs to.

Every request happens inside one workspace, and every row a request creates
belongs to it. Putting that in one place rather than in seventeen repositories
is what makes it hard to forget: a repository that stamps and filters through
these helpers is isolated by construction, and one that does not shows up as
an obvious difference.

`unscoped()` exists for the background worker, which by definition works across
workspaces - it pops whatever the queue hands it. That is a real requirement
rather than a loophole, so it has a name and says what it is for.

The two axes are deliberately not the same mechanism, and mixing them up is
the mistake this file exists to prevent:

* **The workspace is a boundary.** Listing filters by it *and* a lookup by id
  refuses a row from another one. Getting that wrong leaks data between
  tenants.
* **The project is a filing system.** Listing filters by it; a lookup by id
  does not refuse. A report cites a dataset, lineage walks into one, an
  application bundles several — refusing those would break real things in
  order to enforce a rule nobody asked for, and would buy no safety, because
  the workspace is already the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class WorkspaceScope:
    """The workspace, the project and the person a unit of work is acting for."""

    workspace_id: str | None = None
    user_id: str | None = None
    #  None means "every project": what the worker sees, and what a caller
    #  that has not chosen one sees.
    project_id: str | None = None

    @classmethod
    def unscoped(cls) -> WorkspaceScope:
        """For the worker: every workspace, because the queue is shared."""
        return cls()

    @property
    def is_scoped(self) -> bool:
        return self.workspace_id is not None

    @property
    def has_project(self) -> bool:
        return self.project_id is not None

    def within(self, project_id: str | None) -> WorkspaceScope:
        """The same scope, filed under a different project."""
        return replace(self, project_id=project_id)


class WorkspaceScoped:
    """Mixin for a repository that keeps to one workspace, and files by project.

    Four operations, corresponding to the ways the two axes are used: writing
    without an owner, listing without a filter, reading a row by an id that
    came from somewhere else, and listing without a project filter on a table
    that has one.
    """

    session: Session
    scope: WorkspaceScope

    def _file(self, entity: Any) -> Any:
        """Say which project this belongs to, on the entity, before it is stored.

        On the entity and not only on the row, because the entity is what the
        service keeps and later hands back to `update()` — and an update
        rewrites the row from the entity. Filing the row alone worked exactly
        until the first update: `create_from_source` materialises a version and
        then updates the dataset, which wrote the entity's empty project over
        the one that had just been stamped, and the dataset came back unfiled.

        Only when it has not already been told: a dataset materialised from an
        execution is filed where the execution was, which is not necessarily
        where the caller happens to be standing.
        """
        if (
            self.scope.has_project
            and hasattr(entity, "project_id")
            and getattr(entity, "project_id", None) is None
        ):
            entity.project_id = self.scope.project_id
        return entity

    def _stamp(self, row: Any) -> Any:
        """Record which workspace this row belongs to, and who made it.

        Both are row-only facts — the workspace because entities do not carry
        one, the creator because it must never be cleared. The project is not
        here; it is on the entity, which is what `_file` is for.
        """
        if self.scope.is_scoped and hasattr(row, "workspace_id"):
            row.workspace_id = self.scope.workspace_id
        if hasattr(row, "created_by") and getattr(row, "created_by", None) is None:
            row.created_by = self.scope.user_id
        return row

    def _scoped(self, stmt: Any, row_class: Any) -> Any:
        """Restrict a query to this workspace, and to this project where filed.

        A row with no project is shown in every project rather than in none.
        That is what "not filed" has to mean for it to be usable: a model
        somebody deliberately shares, and a run the scheduler made without
        standing anywhere, are both things every project should still see.
        """
        if self.scope.is_scoped and hasattr(row_class, "workspace_id"):
            stmt = stmt.where(row_class.workspace_id == self.scope.workspace_id)
        if self.scope.has_project and hasattr(row_class, "project_id"):
            stmt = stmt.where(
                (row_class.project_id == self.scope.project_id)
                | (row_class.project_id.is_(None))
            )
        return stmt

    def _named(self, stmt: Any, row_class: Any) -> Any:
        """Restrict to this workspace, but *not* to this project.

        A name identifies a resource across the whole workspace — uniqueness is
        `(workspace, name)`, enforced by the services that call this before
        creating anything. Filtering by project here would break that check
        (two projects could then each make a "Sales", and the id-based lookups
        would start returning whichever came first) and would break every
        by-name resolution: an application page asks for the datasets it was
        built on, and it must find them whatever project the viewer happens to
        be standing in. That is what "files, does not fence" means for names.
        """
        if self.scope.is_scoped and hasattr(row_class, "workspace_id"):
            stmt = stmt.where(row_class.workspace_id == self.scope.workspace_id)
        return stmt

    def _fetch(self, row_class: Any, row_id: str) -> Any:
        """Load a row by id, but only if it belongs to this workspace.

        Without this check, a workspace is a listing filter rather than a
        boundary: anybody holding an id from elsewhere could read the row.

        The project is deliberately not checked here. See the module docstring:
        it files, it does not fence.
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
