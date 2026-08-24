"""Creating workspaces, and deciding who may act in one."""

from __future__ import annotations

from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import slugify, utcnow

from ..domain.entities import Role, User
from ..domain.workspaces import (
    DEFAULT_WORKSPACE_ID,
    DEFAULT_WORKSPACE_NAME,
    Workspace,
    WorkspaceMembership,
    WorkspaceRole,
)


class WorkspaceService:
    """Workspaces, their members, and which one a request is acting in."""

    def __init__(self, repository):
        self.repository = repository

    # -- reads -------------------------------------------------------------
    def get(self, workspace_id: str) -> Workspace:
        workspace = self.repository.get(workspace_id) or self.repository.get_by_slug(
            workspace_id
        )
        if not workspace:
            raise NotFoundError(f"workspace '{workspace_id}' not found")
        return workspace

    def list(self) -> list[Workspace]:
        return self.repository.list()

    def list_for(self, user: User) -> list[Workspace]:
        """The workspaces this person can see.

        An administrator sees all of them, because somebody has to be able to
        find a workspace whose members have all left.
        """
        if user.role is Role.ADMIN:
            return self.repository.list()
        ids = {m.workspace_id for m in self.repository.memberships_for(user.id)}
        return [w for w in self.repository.list() if w.id in ids or w.is_default]

    def default(self) -> Workspace:
        """The workspace everything belongs to until told otherwise."""
        existing = self.repository.get_default()
        if existing:
            return existing
        return self.repository.add(
            Workspace(
                id=DEFAULT_WORKSPACE_ID,
                name=DEFAULT_WORKSPACE_NAME,
                slug=slugify(DEFAULT_WORKSPACE_NAME),
                description=(
                    "Everything that existed before workspaces did, and "
                    "everything created without naming one."
                ),
                is_default=True,
            )
        )

    def role_of(self, user: User, workspace_id: str) -> WorkspaceRole | None:
        """What this person may do here, or None if they may not be here.

        A platform administrator is an administrator everywhere: the ability to
        manage the installation is not much use if parts of it are invisible.
        """
        if user.role is Role.ADMIN:
            return WorkspaceRole.ADMIN
        membership = self.repository.member(workspace_id, user.id)
        return membership.role if membership else None

    # -- writes ------------------------------------------------------------
    def create(
        self, *, name: str, description: str = "", created_by: str | None = None
    ) -> Workspace:
        if self.repository.get_by_name(name):
            raise ConflictError(f"a workspace named '{name}' already exists")
        workspace = self.repository.add(
            Workspace(name=name, description=description, created_by=created_by)
        )
        #  Whoever made it can administer it, or nobody could.
        if created_by:
            self.add_member(workspace.id, created_by, WorkspaceRole.ADMIN.value)
        return workspace

    def update(self, workspace_id: str, changes: dict) -> Workspace:
        workspace = self.get(workspace_id)
        for key in ("name", "description"):
            if changes.get(key) is not None:
                setattr(workspace, key, changes[key])
        workspace.updated_at = utcnow()
        return self.repository.update(workspace)

    def delete(self, workspace_id: str) -> None:
        workspace = self.get(workspace_id)
        if workspace.is_default:
            raise ValidationError(
                "the default workspace cannot be deleted: it owns everything "
                "that names no workspace"
            )
        self.repository.delete(workspace.id)

    def add_member(
        self, workspace_id: str, user_id: str, role: str
    ) -> WorkspaceMembership:
        workspace = self.get(workspace_id)
        if self.repository.member(workspace.id, user_id):
            raise ConflictError("that person is already in this workspace")
        return self.repository.add_member(
            WorkspaceMembership(
                workspace_id=workspace.id,
                user_id=user_id,
                role=WorkspaceRole(role),
            )
        )

    def members(self, workspace_id: str) -> list[WorkspaceMembership]:
        return self.repository.members(self.get(workspace_id).id)

    def remove_member(self, workspace_id: str, user_id: str) -> None:
        self.repository.remove_member(self.get(workspace_id).id, user_id)
