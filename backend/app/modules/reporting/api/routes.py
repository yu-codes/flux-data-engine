"""Report API: compose, render and export."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Response, status
from pydantic import Field

from app.api.deps import AuditServiceDep, ReportServiceDep
from app.api.schema_base import ApiModel
from app.api.security import CurrentUser

from ..domain.entities import ExportFormat, Report, SectionKind

router = APIRouter(tags=["results"])


class SectionIn(ApiModel):
    kind: str
    title: str = ""
    body: str = ""
    execution_id: str | None = None
    result_id: str | None = None
    dataset_version_id: str | None = None
    visualization_id: str | None = None
    model_id: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ReportCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    sections: list[SectionIn] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ReportUpdate(ApiModel):
    name: str | None = None
    description: str | None = None
    sections: list[SectionIn] | None = None
    tags: list[str] | None = None
    status: str | None = None


class ReportOut(ApiModel):
    id: str
    name: str
    description: str
    sections: list[dict[str, Any]]
    status: str
    tags: list[str]
    last_export_uri: str | None
    last_export_format: str | None
    last_exported_at: datetime | None
    created_at: datetime
    updated_at: datetime


@router.get("/report-sections", summary="Section kinds a report can contain")
def section_kinds():
    return {
        "kinds": [k.value for k in SectionKind],
        "formats": [f.value for f in ExportFormat],
    }


@router.get("/reports", response_model=list[ReportOut])
def list_reports(service: ReportServiceDep):
    return [_out(r) for r in service.list()]


@router.post("/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreate,
    service: ReportServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
):
    report = service.create(
        name=payload.name,
        description=payload.description,
        sections=[s.model_dump() for s in payload.sections],
        tags=payload.tags,
    )
    audit.record(
        action="report.create", resource_type="report",
        resource_id=report.id, actor=user,
    )
    return _out(report)


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: str, service: ReportServiceDep):
    return _out(service.get(report_id))


@router.patch("/reports/{report_id}", response_model=ReportOut)
def update_report(report_id: str, payload: ReportUpdate, service: ReportServiceDep):
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("sections") is not None:
        changes["sections"] = [dict(section) for section in changes["sections"]]
    return _out(service.update(report_id, changes))


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: str,
    service: ReportServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
) -> None:
    service.delete(report_id)
    audit.record(
        action="report.delete", resource_type="report",
        resource_id=report_id, actor=user,
    )


@router.get("/reports/{report_id}/render", summary="Resolve sections against live data")
def render_report(report_id: str, service: ReportServiceDep):
    return service.render(report_id)


@router.get(
    "/reports/{report_id}/export",
    summary="Export a snapshot and return it as a file",
    response_class=Response,
)
def export_report(
    report_id: str,
    service: ReportServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
    fmt: str = Query(ExportFormat.MARKDOWN.value, alias="format"),
    download: bool = Query(True),
):
    exported = service.export(report_id, fmt)
    audit.record(
        action="report.export", resource_type="report", resource_id=report_id,
        actor=user, detail={"format": exported["format"]},
    )
    headers = {}
    if download:
        suffix = {"markdown": "md", "html": "html", "json": "json"}[exported["format"]]
        headers["content-disposition"] = (
            f'attachment; filename="report-{report_id}.{suffix}"'
        )
    return Response(
        content=exported["content"],
        media_type=f"{exported['media_type']}; charset=utf-8",
        headers=headers,
    )


def _out(report: Report) -> ReportOut:
    return ReportOut(
        id=report.id,
        name=report.name,
        description=report.description,
        sections=[s.to_dict() for s in report.sections],
        status=report.status.value,
        tags=report.tags,
        last_export_uri=report.last_export_uri,
        last_export_format=report.last_export_format,
        last_exported_at=report.last_exported_at,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )
