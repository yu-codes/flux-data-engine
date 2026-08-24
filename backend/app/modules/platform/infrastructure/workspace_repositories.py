"""SQL persistence for workspaces and who is in them.

Not workspace-scoped itself, which would be circular: this is the table that
decides what a scope means.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.workspaces import Workspace, WorkspaceMembership
from . import workspace_orm as orm


class SqlWorkspaceRepository:
    def __init__(self, session: Session):
        self.session = session

    # -- workspaces --------------------------------------------------------
    def add(self, workspace: Workspace) -> Workspace:
        self.session.add(orm.workspace_to_row(workspace))
        self.session.flush()
        return workspace

    def get(self, workspace_id: str) -> Workspace | None:
        row = self.session.get(orm.WorkspaceRow, workspace_id)
        return orm.workspace_to_entity(row) if row else None

    def get_by_slug(self, slug: str) -> Workspace | None:
        row = self.session.scalar(
            select(orm.WorkspaceRow).where(orm.WorkspaceRow.slug == slug)
        )
        return orm.workspace_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Workspace | None:
        row = self.session.scalar(
            select(orm.WorkspaceRow).where(orm.WorkspaceRow.name == name)
        )
        return orm.workspace_to_entity(row) if row else None

    def get_default(self) -> Workspace | None:
        row = self.session.scalar(
            select(orm.WorkspaceRow).where(orm.WorkspaceRow.is_default.is_(True))
        )
        return orm.workspace_to_entity(row) if row else None

    def update(self, workspace: Workspace) -> Workspace:
        row = self.session.get(orm.WorkspaceRow, workspace.id)
        if row is None:
            return self.add(workspace)
        orm.workspace_to_row(workspace, row)
        self.session.flush()
        return orm.workspace_to_entity(row)

    def list(self) -> list[Workspace]:
        stmt = select(orm.WorkspaceRow).order_by(orm.WorkspaceRow.created_at)
        return [orm.workspace_to_entity(row) for row in self.session.scalars(stmt)]

    def delete(self, workspace_id: str) -> None:
        row = self.session.get(orm.WorkspaceRow, workspace_id)
        if row is not None:
            self.session.delete(row)
            self.session.flush()

    # -- membership --------------------------------------------------------
    def add_member(self, membership: WorkspaceMembership) -> WorkspaceMembership:
        self.session.add(orm.member_to_row(membership))
        self.session.flush()
        return membership

    def member(self, workspace_id: str, user_id: str) -> WorkspaceMembership | None:
        row = self.session.scalar(
            select(orm.WorkspaceMemberRow).where(
                orm.WorkspaceMemberRow.workspace_id == workspace_id,
                orm.WorkspaceMemberRow.user_id == user_id,
            )
        )
        return orm.member_to_entity(row) if row else None

    def members(self, workspace_id: str) -> list[WorkspaceMembership]:
        stmt = select(orm.WorkspaceMemberRow).where(
            orm.WorkspaceMemberRow.workspace_id == workspace_id
        )
        return [orm.member_to_entity(row) for row in self.session.scalars(stmt)]

    def memberships_for(self, user_id: str) -> list[WorkspaceMembership]:
        stmt = select(orm.WorkspaceMemberRow).where(
            orm.WorkspaceMemberRow.user_id == user_id
        )
        return [orm.member_to_entity(row) for row in self.session.scalars(stmt)]

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        row = self.session.scalar(
            select(orm.WorkspaceMemberRow).where(
                orm.WorkspaceMemberRow.workspace_id == workspace_id,
                orm.WorkspaceMemberRow.user_id == user_id,
            )
        )
        if row is not None:
            self.session.delete(row)
            self.session.flush()
