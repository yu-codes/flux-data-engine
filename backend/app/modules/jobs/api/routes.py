"""Jobs API, including the stream a waiting page watches."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from app.api.deps import JobServiceDep, ServicesDep
from app.api.schema_base import ApiModel
from app.core.database import session_scope

from ..domain.entities import Job
from .stream import watch

router = APIRouter(tags=["jobs"])

class JobOut(ApiModel):
    id: str
    kind: str
    target_id: str
    parameters: dict[str, Any]
    status: str
    outcome: dict[str, Any]
    error: str | None
    attempts: int
    duration_seconds: float | None
    requested_by: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        kind=job.kind,
        target_id=job.target_id,
        parameters=job.parameters,
        status=job.status.value,
        outcome=job.outcome,
        error=job.error,
        attempts=job.attempts,
        duration_seconds=job.duration_seconds,
        requested_by=job.requested_by,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    service: JobServiceDep,
    kind: str | None = Query(None),
    target_id: str | None = Query(None),
    job_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
):
    return [
        _job_out(job)
        for job in service.list(
            kind=kind, target_id=target_id, status=job_status, limit=limit
        )
    ]


@router.get("/job-kinds", summary="What kinds of background work this build runs")
def list_job_kinds(service: JobServiceDep) -> dict:
    return {"kinds": service.kinds()}


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, service: JobServiceDep):
    return _job_out(service.get(job_id))


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, service: JobServiceDep):
    return _job_out(service.cancel(job_id))


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run the same work again as a new job",
)
def retry_job(job_id: str, service: JobServiceDep):
    """A new job rather than a reset of the old one.

    The failed attempt is part of the record: what was tried, when, and why it
    did not work. Resetting it in place would erase the only evidence.
    """
    original = service.get(job_id)
    return _job_out(
        service.submit(
            kind=original.kind,
            target_id=original.target_id,
            parameters=original.parameters,
            requested_by=original.requested_by,
        )
    )


@router.get(
    "/jobs/{job_id}/events",
    summary="Server-sent events for one job, until it finishes",
)
async def job_events(job_id: str, services: ServicesDep):
    """Stream a job's status until it reaches a terminal state."""
    #  Fail fast on a job that does not exist rather than streaming nothing.
    services.jobs.get(job_id)
    return StreamingResponse(
        watch(lambda: _snapshot(job_id)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _snapshot(job_id: str) -> tuple[dict, bool]:
    """Read the job from its own session.

    The stream outlives the request's transaction by design, so it cannot hold
    the service the request was given.
    """
    from app.core.container import build_services

    with session_scope() as session:
        job = build_services(session).jobs.get(job_id)
        return _job_out(job).model_dump(mode="json"), job.status.is_terminal


#  Proxies buffer streamed responses by default, which turns an event stream
#  into one long silence followed by everything at once.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
