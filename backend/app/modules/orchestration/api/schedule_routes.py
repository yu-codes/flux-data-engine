"""Schedules and the audit trail."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.api.deps import AuditServiceDep, ScheduleServiceDep
from app.api.schema_base import ApiModel
from app.api.security import CurrentUser

from ..domain.schedules import MIN_INTERVAL_SECONDS, Schedule, ScheduleStatus

router = APIRouter(tags=["platform"])


class ScheduleCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    #  What to fire. A model runs as one Execution; a pipeline runs as a Job.
    target_id: str
    target_type: str = "model"
    kind: str = "prediction"
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    cron: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ScheduleUpdate(ApiModel):
    description: str | None = None
    kind: str | None = None
    interval_seconds: int | None = Field(default=None, ge=MIN_INTERVAL_SECONDS)
    cron: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    input_payload: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None
    status: str | None = None


class ScheduleOut(ApiModel):
    id: str
    name: str
    description: str
    target_id: str
    target_type: str
    kind: str
    interval_seconds: int | None
    cron: str | None
    dataset_id: str | None
    dataset_version_id: str | None
    input_payload: dict[str, Any]
    parameters: dict[str, Any]
    status: str
    last_run_at: datetime | None
    last_execution_id: str | None
    last_status: str | None
    last_error: str | None
    next_run_at: datetime | None
    run_count: int
    failure_count: int
    created_at: datetime


class PreviewRequest(ApiModel):
    interval_seconds: int | None = None
    cron: str | None = None
    count: int = Field(default=5, ge=1, le=20)


class AuditOut(ApiModel):
    id: str
    action: str
    resource_type: str
    resource_id: str | None
    actor_id: str | None
    actor_email: str | None
    detail: dict[str, Any]
    outcome: str
    created_at: datetime


# --------------------------------------------------------------------------
# schedules
# --------------------------------------------------------------------------
@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(
    service: ScheduleServiceDep, schedule_status: str | None = Query(None, alias="status")
):
    return [_out(s) for s in service.list(status=schedule_status)]


@router.post(
    "/schedules", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED
)
def create_schedule(
    payload: ScheduleCreate,
    service: ScheduleServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
):
    schedule = service.create(
        name=payload.name,
        target_id=payload.target_id,
        target_type=payload.target_type,
        kind=payload.kind,
        interval_seconds=payload.interval_seconds,
        cron=payload.cron,
        dataset_id=payload.dataset_id,
        dataset_version_id=payload.dataset_version_id,
        input_payload=payload.input,
        parameters=payload.parameters,
        description=payload.description,
        created_by=user.id,
    )
    audit.record(
        action="schedule.create", resource_type="schedule",
        resource_id=schedule.id, actor=user,
        detail={"target": f"{schedule.target_type.value}:{schedule.target_id}",
                "cron": schedule.cron,
                "interval_seconds": schedule.interval_seconds},
    )
    return _out(schedule)


@router.post("/schedules/preview", summary="Next fire times for a trigger")
def preview_schedule(payload: PreviewRequest, service: ScheduleServiceDep):
    moments = service.preview(
        cron=payload.cron,
        interval_seconds=payload.interval_seconds,
        count=payload.count,
    )
    return {"next_runs": [m.isoformat() for m in moments]}


@router.get("/schedules/{schedule_id}", response_model=ScheduleOut)
def get_schedule(schedule_id: str, service: ScheduleServiceDep):
    return _out(service.get(schedule_id))


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: str, payload: ScheduleUpdate, service: ScheduleServiceDep
):
    return _out(service.update(schedule_id, payload.model_dump(exclude_unset=True)))


@router.post("/schedules/{schedule_id}/pause", response_model=ScheduleOut)
def pause_schedule(schedule_id: str, service: ScheduleServiceDep):
    return _out(service.pause(schedule_id))


@router.post("/schedules/{schedule_id}/resume", response_model=ScheduleOut)
def resume_schedule(schedule_id: str, service: ScheduleServiceDep):
    return _out(service.resume(schedule_id))


@router.post(
    "/schedules/{schedule_id}/run",
    response_model=ScheduleOut,
    summary="Fire now, without changing the cadence",
)
def run_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
):
    schedule = service.run_now(schedule_id)
    audit.record(
        action="schedule.run", resource_type="schedule",
        resource_id=schedule.id, actor=user,
        detail={"execution_id": schedule.last_execution_id},
    )
    return _out(schedule)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: str,
    service: ScheduleServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
) -> None:
    service.delete(schedule_id)
    audit.record(
        action="schedule.delete", resource_type="schedule",
        resource_id=schedule_id, actor=user,
    )


@router.get("/schedule-statuses", summary="Schedule status vocabulary")
def schedule_statuses():
    return {
        "statuses": [s.value for s in ScheduleStatus],
        "min_interval_seconds": MIN_INTERVAL_SECONDS,
    }


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
@router.get("/audit", response_model=list[AuditOut], summary="Recorded changes")
def list_audit(
    service: AuditServiceDep,
    actor_id: str | None = Query(None),
    resource_type: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    filters: dict[str, Any] = {"limit": limit}
    if actor_id:
        filters["actor_id"] = actor_id
    if resource_type:
        filters["resource_type"] = resource_type
    if action:
        filters["action"] = action
    return [
        AuditOut(
            id=entry.id,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            actor_id=entry.actor_id,
            actor_email=entry.actor_email,
            detail=entry.detail,
            outcome=entry.outcome,
            created_at=entry.created_at,
        )
        for entry in service.list(**filters)
    ]


def _out(schedule: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        name=schedule.name,
        description=schedule.description,
        target_id=schedule.target_id,
        target_type=schedule.target_type.value,
        kind=schedule.kind,
        interval_seconds=schedule.interval_seconds,
        cron=schedule.cron,
        dataset_id=schedule.dataset_id,
        dataset_version_id=schedule.dataset_version_id,
        input_payload=schedule.input_payload,
        parameters=schedule.parameters,
        status=schedule.status.value,
        last_run_at=schedule.last_run_at,
        last_execution_id=schedule.last_execution_id,
        last_status=schedule.last_status,
        last_error=schedule.last_error,
        next_run_at=schedule.next_run_at,
        run_count=schedule.run_count,
        failure_count=schedule.failure_count,
        created_at=schedule.created_at,
    )
