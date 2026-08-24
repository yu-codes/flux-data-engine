"""Analysis API: explore, visualise, assemble dashboards."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, status
from pydantic import Field

from app.api.deps import (
    DashboardServiceDep,
    ExploreServiceDep,
    VisualizationServiceDep,
)
from app.api.schema_base import ApiModel

from ..domain.entities import Aggregation, ChartSpec, ChartType

router = APIRouter(tags=["analysis"])


class QueryRequest(ApiModel):
    columns: list[str] | None = None
    filters: list[dict[str, Any]] = Field(default_factory=list)
    sort_by: str | None = None
    sort_desc: bool = False
    limit: int = Field(default=200, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)


class VisualizationCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    spec: dict[str, Any]
    dataset_version_id: str | None = None
    dataset_id: str | None = None
    result_id: str | None = None
    description: str = ""


class VisualizationUpdate(ApiModel):
    name: str | None = None
    description: str | None = None
    spec: dict[str, Any] | None = None


class VisualizationOut(ApiModel):
    id: str
    name: str
    description: str
    spec: dict[str, Any]
    dataset_id: str | None
    dataset_version_id: str | None
    result_id: str | None
    created_at: datetime


class DashboardCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    tiles: list[dict[str, Any]] = Field(default_factory=list)


class DashboardUpdate(ApiModel):
    name: str | None = None
    description: str | None = None
    tiles: list[dict[str, Any]] | None = None


class DashboardOut(ApiModel):
    id: str
    name: str
    description: str
    tiles: list[dict[str, Any]]
    created_at: datetime


class TileCreate(ApiModel):
    visualization_id: str
    width: int = Field(default=6, ge=3, le=12)
    height: int = Field(default=4, ge=2, le=12)


class TileUpdate(ApiModel):
    width: int | None = Field(default=None, ge=3, le=12)
    height: int | None = Field(default=None, ge=2, le=12)
    #  -1 or +1: shift the tile one place earlier or later in reading order.
    move: int | None = Field(default=None, ge=-1, le=1)


# --------------------------------------------------------------------------
# explore
# --------------------------------------------------------------------------
@router.get("/chart-options", summary="Chart types and aggregations")
def chart_options():
    return {
        "chart_types": [t.value for t in ChartType],
        "aggregations": [a.value for a in Aggregation],
    }


@router.get("/explore/{version_id}/profile", summary="Per-column data profile")
def profile_version(version_id: str, service: ExploreServiceDep):
    return service.profile(version_id)


@router.post("/explore/{version_id}/query", summary="Filter, sort and page a dataset")
def query_version(version_id: str, payload: QueryRequest, service: ExploreServiceDep):
    return service.query(
        version_id,
        columns=payload.columns,
        filters=payload.filters,
        sort_by=payload.sort_by,
        sort_desc=payload.sort_desc,
        limit=payload.limit,
        offset=payload.offset,
    )


@router.post("/explore/{version_id}/series", summary="Chart-ready series from a spec")
def series_version(version_id: str, spec: dict[str, Any], service: ExploreServiceDep):
    return service.series(version_id, ChartSpec.from_dict(spec))


# --------------------------------------------------------------------------
# visualizations
# --------------------------------------------------------------------------
@router.get("/visualizations", response_model=list[VisualizationOut])
def list_visualizations(service: VisualizationServiceDep):
    return [_viz_out(v) for v in service.list()]


@router.post(
    "/visualizations", response_model=VisualizationOut, status_code=status.HTTP_201_CREATED
)
def create_visualization(payload: VisualizationCreate, service: VisualizationServiceDep):
    return _viz_out(
        service.create(
            name=payload.name,
            spec=payload.spec,
            dataset_version_id=payload.dataset_version_id,
            dataset_id=payload.dataset_id,
            result_id=payload.result_id,
            description=payload.description,
        )
    )


@router.get("/visualizations/{visualization_id}", response_model=VisualizationOut)
def get_visualization(visualization_id: str, service: VisualizationServiceDep):
    return _viz_out(service.get(visualization_id))


@router.patch("/visualizations/{visualization_id}", response_model=VisualizationOut)
def update_visualization(
    visualization_id: str,
    payload: VisualizationUpdate,
    service: VisualizationServiceDep,
):
    return _viz_out(
        service.update(visualization_id, payload.model_dump(exclude_unset=True))
    )


@router.get("/visualizations/{visualization_id}/render")
def render_visualization(visualization_id: str, service: VisualizationServiceDep):
    return service.render(visualization_id)


@router.delete(
    "/visualizations/{visualization_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_visualization(visualization_id: str, service: VisualizationServiceDep) -> None:
    service.delete(visualization_id)


# --------------------------------------------------------------------------
# dashboards
# --------------------------------------------------------------------------
@router.get("/dashboards", response_model=list[DashboardOut])
def list_dashboards(service: DashboardServiceDep):
    return [_dash_out(d) for d in service.list()]


@router.post(
    "/dashboards", response_model=DashboardOut, status_code=status.HTTP_201_CREATED
)
def create_dashboard(payload: DashboardCreate, service: DashboardServiceDep):
    return _dash_out(
        service.create(
            name=payload.name, description=payload.description, tiles=payload.tiles
        )
    )


@router.get("/dashboards/{dashboard_id}", response_model=DashboardOut)
def get_dashboard(dashboard_id: str, service: DashboardServiceDep):
    return _dash_out(service.get(dashboard_id))


@router.patch("/dashboards/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(
    dashboard_id: str, payload: DashboardUpdate, service: DashboardServiceDep
):
    return _dash_out(service.update(dashboard_id, payload.model_dump(exclude_unset=True)))


@router.get("/dashboards/{dashboard_id}/render")
def render_dashboard(dashboard_id: str, service: DashboardServiceDep):
    return service.render(dashboard_id)


@router.post(
    "/dashboards/{dashboard_id}/tiles",
    response_model=DashboardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a chart to an existing dashboard",
)
def add_dashboard_tile(
    dashboard_id: str, payload: TileCreate, service: DashboardServiceDep
):
    return _dash_out(
        service.add_tile(
            dashboard_id,
            visualization_id=payload.visualization_id,
            width=payload.width,
            height=payload.height,
        )
    )


@router.patch(
    "/dashboards/{dashboard_id}/tiles/{visualization_id}", response_model=DashboardOut
)
def update_dashboard_tile(
    dashboard_id: str,
    visualization_id: str,
    payload: TileUpdate,
    service: DashboardServiceDep,
):
    if payload.move is not None:
        return _dash_out(service.move_tile(dashboard_id, visualization_id, payload.move))
    return _dash_out(
        service.update_tile(
            dashboard_id,
            visualization_id,
            payload.model_dump(exclude_unset=True, exclude={"move"}),
        )
    )


@router.delete(
    "/dashboards/{dashboard_id}/tiles/{visualization_id}", response_model=DashboardOut
)
def remove_dashboard_tile(
    dashboard_id: str, visualization_id: str, service: DashboardServiceDep
):
    return _dash_out(service.remove_tile(dashboard_id, visualization_id))


@router.delete("/dashboards/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(dashboard_id: str, service: DashboardServiceDep) -> None:
    service.delete(dashboard_id)


def _viz_out(entity) -> VisualizationOut:
    return VisualizationOut(
        id=entity.id,
        name=entity.name,
        description=entity.description,
        spec=entity.spec.to_dict(),
        dataset_id=entity.dataset_id,
        dataset_version_id=entity.dataset_version_id,
        result_id=entity.result_id,
        created_at=entity.created_at,
    )


def _dash_out(entity) -> DashboardOut:
    return DashboardOut(
        id=entity.id,
        name=entity.name,
        description=entity.description,
        tiles=[t.to_dict() for t in entity.tiles],
        created_at=entity.created_at,
    )
