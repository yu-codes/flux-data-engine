"""Workspaces API."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import WorkspaceServiceDep
from app.api.schema_base import ApiModel
from app.api.security import AdminUser, CurrentUser

from ..domain.workspaces import Workspace, WorkspaceMembership

router = APIRouter(tags=["workspaces"])


class WorkspaceCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class WorkspaceUpdate(ApiModel):
    name: str | None = None
    description: str | None = None


class WorkspaceOut(ApiModel):
    id: str
    name: str
    slug: str
    description: str
    is_default: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class MemberIn(ApiModel):
    user_id: str
    role: str = Field(default="viewer", pattern="^(admin|editor|viewer)$")


class MemberOut(ApiModel):
    id: str
    workspace_id: str
    user_id: str
    role: str
    created_at: datetime


def _out(workspace: Workspace) -> WorkspaceOut:
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        description=workspace.description,
        is_default=workspace.is_default,
        created_by=workspace.created_by,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )


def _member_out(membership: WorkspaceMembership) -> MemberOut:
    return MemberOut(
        id=membership.id,
        workspace_id=membership.workspace_id,
        user_id=membership.user_id,
        role=membership.role.value,
        created_at=membership.created_at,
    )


@router.get(
    "/workspaces",
    response_model=list[WorkspaceOut],
    summary="The workspaces you can act in",
)
def list_workspaces(service: WorkspaceServiceDep, user: CurrentUser):
    return [_out(w) for w in service.list_for(user)]


@router.post(
    "/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED
)
def create_workspace(
    payload: WorkspaceCreate, service: WorkspaceServiceDep, user: AdminUser
):
    return _out(
        service.create(
            name=payload.name, description=payload.description, created_by=user.id
        )
    )


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: str, service: WorkspaceServiceDep):
    return _out(service.get(workspace_id))


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdate,
    service: WorkspaceServiceDep,
    user: AdminUser,
):
    return _out(service.update(workspace_id, payload.model_dump(exclude_unset=True)))


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: str, service: WorkspaceServiceDep, user: AdminUser
) -> None:
    service.delete(workspace_id)


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_members(workspace_id: str, service: WorkspaceServiceDep):
    return [_member_out(m) for m in service.members(workspace_id)]


@router.post(
    "/workspaces/{workspace_id}/members",
    response_model=MemberOut,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    workspace_id: str,
    payload: MemberIn,
    service: WorkspaceServiceDep,
    user: AdminUser,
):
    return _member_out(service.add_member(workspace_id, payload.user_id, payload.role))


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_member(
    workspace_id: str, user_id: str, service: WorkspaceServiceDep, user: AdminUser
) -> None:
    service.remove_member(workspace_id, user_id)
