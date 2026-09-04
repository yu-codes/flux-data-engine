"""Projects API.

A project is chosen with the `X-Project` header, exactly as a workspace is
chosen with `X-Workspace`. These endpoints are how the switcher is populated
and how a new piece of work is started.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import ProjectServiceDep
from app.api.schema_base import ApiModel
from app.api.security import CurrentUser

from ..domain.projects import Project

router = APIRouter(tags=["projects"])


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    #  Optional: derived from the name when absent, which is what almost
    #  everybody wants. Offered because a directory is a thing on somebody's
    #  disk and they may already have one.
    directory: str | None = None


class ProjectUpdate(ApiModel):
    name: str | None = None
    description: str | None = None
    directory: str | None = None


class ProjectOut(ApiModel):
    id: str
    workspace_id: str | None
    name: str
    slug: str
    description: str
    directory: str
    sources_path: str
    uploads_path: str
    is_default: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime


def _out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        workspace_id=project.workspace_id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        directory=project.directory,
        sources_path=project.sources_path,
        uploads_path=project.uploads_path,
        is_default=project.is_default,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/projects", response_model=list[ProjectOut], summary="Projects here")
def list_projects(service: ProjectServiceDep):
    #  Asking for the list is what creates the default on a fresh install, so
    #  the switcher always has something to show.
    service.default()
    return [_out(project) for project in service.list()]


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, service: ProjectServiceDep):
    return _out(service.get(project_id))


@router.get(
    "/projects/{project_id}/holdings",
    summary="What this project holds, by kind",
)
def project_holdings(project_id: str, service: ProjectServiceDep):
    """Read before offering to delete: a project that holds things is refused."""
    project = service.get(project_id)
    return {"project_id": project.id, "holds": service.holdings(project.id)}


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new piece of work",
)
def create_project(payload: ProjectCreate, service: ProjectServiceDep, user: CurrentUser):
    return _out(
        service.create(
            name=payload.name,
            description=payload.description,
            directory=payload.directory,
            created_by=user.id,
        )
    )


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectUpdate, service: ProjectServiceDep):
    return _out(
        service.update(
            project_id,
            payload.model_dump(exclude_unset=True, exclude_none=True),
        )
    )


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str, service: ProjectServiceDep):
    service.delete(project_id)
