"""Applications API.

An application is a saved bundle of models, datasets and dashboards with an
entrypoint. Publishing makes it reachable; unpublishing stops offering it.
There is no separate deployment: nothing is stood up, so nothing is torn
down, and a second name for the same state only obscured that.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import (
    ApplicationServiceDep,
    DashboardServiceDep,
    DatasetServiceDep,
    ModelServiceDep,
)
from app.api.schema_base import ApiModel

from .rendering import render_application

router = APIRouter(tags=["applications"])


class ApplicationCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    kind: str = "composed"
    model_ids: list[str] = Field(default_factory=list)
    dataset_ids: list[str] = Field(default_factory=list)
    dashboard_ids: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)
    entrypoint: str | None = None


class ApplicationUpdate(ApiModel):
    description: str | None = None
    status: str | None = None
    model_ids: list[str] | None = None
    dataset_ids: list[str] | None = None
    dashboard_ids: list[str] | None = None
    configuration: dict[str, Any] | None = None
    entrypoint: str | None = None


class ApplicationOut(ApiModel):
    id: str
    name: str
    slug: str
    kind: str
    description: str
    status: str
    model_ids: list[str]
    dataset_ids: list[str]
    dashboard_ids: list[str]
    configuration: dict[str, Any]
    entrypoint: str | None
    visibility: str = "workspace"
    is_shared: bool = False
    created_at: datetime
    updated_at: datetime




@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(service: ApplicationServiceDep):
    return [_app_out(a) for a in service.list()]


@router.post(
    "/applications", response_model=ApplicationOut, status_code=status.HTTP_201_CREATED
)
def create_application(payload: ApplicationCreate, service: ApplicationServiceDep):
    return _app_out(
        service.create(
            name=payload.name,
            description=payload.description,
            kind=payload.kind,
            model_ids=payload.model_ids,
            dataset_ids=payload.dataset_ids,
            dashboard_ids=payload.dashboard_ids,
            configuration=payload.configuration,
            entrypoint=payload.entrypoint,
        )
    )


@router.get("/applications/{application_id}", response_model=ApplicationOut)
def get_application(application_id: str, service: ApplicationServiceDep):
    return _app_out(service.get(application_id))


@router.get(
    "/applications/{application_id}/view",
    summary="Open an application: its dashboards, drawn",
)
def view_application(
    application_id: str,
    applications: ApplicationServiceDep,
    dashboards: DashboardServiceDep,
    models: ModelServiceDep,
    datasets: DatasetServiceDep,
) -> dict[str, Any]:
    """What an application looks like when somebody opens it.

    Composed applications used to have no page at all: you could build one,
    publish it and share it, and the only way to look at it yourself was to
    open the share link. The same rendering as the shared view, so what you
    send somebody is what you saw.
    """
    return render_application(
        applications.get(application_id), dashboards, models=models, datasets=datasets
    )


@router.patch("/applications/{application_id}", response_model=ApplicationOut)
def update_application(
    application_id: str, payload: ApplicationUpdate, service: ApplicationServiceDep
):
    return _app_out(service.update(application_id, payload.model_dump(exclude_unset=True)))


@router.post("/applications/{application_id}/unpublish", response_model=ApplicationOut)
def unpublish_application(application_id: str, service: ApplicationServiceDep):
    """Stop offering it. Its models, datasets and dashboards are untouched."""
    return _app_out(service.unpublish(application_id))


@router.post("/applications/{application_id}/publish", response_model=ApplicationOut)
def publish_application(application_id: str, service: ApplicationServiceDep):
    return _app_out(service.publish(application_id))


class ShareOut(ApiModel):
    """The link, and what it grants."""

    share_url: str
    token: str
    visibility: str
    shared_at: datetime | None


@router.post(
    "/applications/{application_id}/share",
    response_model=ShareOut,
    summary="Create a link anybody can open, without an account",
)
def share_application(application_id: str, service: ApplicationServiceDep):
    application = service.share(application_id)
    return ShareOut(
        #  Relative, because the platform does not reliably know the address it
        #  is reached at - a proxy, a port mapping and a hostname are all the
        #  deployment's business, not the application's.
        share_url=f"/shared/{application.share_token}",
        token=application.share_token or "",
        visibility=application.visibility.value,
        shared_at=application.shared_at,
    )


@router.delete(
    "/applications/{application_id}/share",
    response_model=ApplicationOut,
    summary="Revoke the link. The old URL stops working.",
)
def unshare_application(application_id: str, service: ApplicationServiceDep):
    return _app_out(service.unshare(application_id))


@router.delete("/applications/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(application_id: str, service: ApplicationServiceDep) -> None:
    service.delete(application_id)


def _app_out(application) -> ApplicationOut:
    return ApplicationOut(
        id=application.id,
        name=application.name,
        slug=application.slug,
        kind=application.kind.value,
        description=application.description,
        status=application.status.value,
        model_ids=application.model_ids,
        dataset_ids=application.dataset_ids,
        dashboard_ids=application.dashboard_ids,
        configuration=application.configuration,
        entrypoint=application.entrypoint,
        visibility=application.visibility.value,
        is_shared=application.is_shared,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )
