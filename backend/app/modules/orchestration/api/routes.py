"""Pipeline API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field

from app.api.deps import AuditServiceDep, JobServiceDep, PipelineServiceDep
from app.api.schema_base import ApiModel
from app.api.security import CurrentUser

from ..application.from_query import steps_from_query
from ..domain.entities import Pipeline, PipelineRun

router = APIRouter(tags=["orchestration"])


class StepIn(ApiModel):
    """One step, described either inline or by reference.

    Inline - `provider` plus `configuration` - is the normal way, and the step
    stays part of the pipeline. `model_id` is for a step that deliberately runs
    something from the library, so improving that model improves every pipeline
    using it. `pipeline_id` runs another pipeline, so a shared preparation is
    kept in one place instead of copied into everything that needs it.
    """

    name: str = Field(min_length=1, max_length=128)
    model_id: str | None = None
    pipeline_id: str | None = Field(
        default=None,
        description="run another pipeline as this step",
    )
    provider: str | None = Field(
        default=None,
        description="run this provider with the configuration below",
    )
    configuration: dict[str, Any] = Field(default_factory=dict)
    kind: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    input_from: str | None = Field(
        default=None,
        description="a previous step's name; omit to read the pipeline's input dataset",
    )
    inputs: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "extra inputs wired by name to an earlier step, e.g. "
            "{\"right\": \"load prices\"}. A step with these is where the "
            "graph merges."
        ),
    )
    input_datasets: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "extra inputs that are datasets, e.g. {\"right\": \"ds_...\"} - "
            "for joining against a reference table the pipeline does not derive."
        ),
    )
    materialise: bool = Field(
        default=False,
        description=(
            "keep this step's output as a Dataset of its own. Off by default: "
            "a step in the middle of a run is working state, and publishing "
            "every one of them is what fills the catalogue with noise."
        ),
    )
    description: str = ""


class PipelineCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    input_dataset_id: str
    steps: list[StepIn] = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class PipelineUpdate(ApiModel):
    description: str | None = None
    tags: list[str] | None = None
    input_dataset_id: str | None = None
    steps: list[StepIn] | None = None
    status: str | None = None


class PipelineOut(ApiModel):
    id: str
    name: str
    description: str
    input_dataset_id: str
    steps: list[dict[str, Any]]
    status: str
    tags: list[str]
    last_run_id: str | None
    last_run_status: str | None
    created_at: datetime
    updated_at: datetime
    #  Where this is filed. Null means shared: it shows under every project
    #  rather than none, which is what the library relies on.
    project_id: str | None = None


class PipelineRunOut(ApiModel):
    id: str
    pipeline_id: str
    status: str
    input_dataset_version_id: str | None
    #  What the pipeline was when this run started, so a reader looking at an
    #  old run is not shown today's steps.
    definition_snapshot: dict[str, Any]
    execution_id: str | None
    step_runs: list[dict[str, Any]]
    output_dataset_ids: list[str]
    error: str | None
    duration_seconds: float | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class RunRequest(ApiModel):
    dataset_version_id: str | None = None


@router.get("/pipelines", response_model=list[PipelineOut])
def list_pipelines(service: PipelineServiceDep):
    return [_out(p) for p in service.list()]


@router.post(
    "/pipelines", response_model=PipelineOut, status_code=status.HTTP_201_CREATED
)
def create_pipeline(
    payload: PipelineCreate,
    service: PipelineServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
):
    pipeline = service.create(
        name=payload.name,
        input_dataset_id=payload.input_dataset_id,
        steps=[step.model_dump() for step in payload.steps],
        description=payload.description,
        tags=payload.tags,
    )
    audit.record(
        action="pipeline.create", resource_type="pipeline",
        resource_id=pipeline.id, actor=user,
        detail={"steps": [s.name for s in pipeline.steps]},
    )
    return _out(pipeline)


class PipelineFromQuery(ApiModel):
    """An Explore query, plus what to call the pipeline it becomes."""

    name: str = Field(min_length=1, max_length=255)
    dataset_id: str
    description: str = ""
    columns: list[str] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    sort_by: str | None = None
    sort_desc: bool = False


@router.post(
    "/pipelines/from-query",
    response_model=PipelineOut,
    status_code=status.HTTP_201_CREATED,
    summary="Keep what Explore is showing, as a pipeline",
)
def create_pipeline_from_query(
    payload: PipelineFromQuery,
    service: PipelineServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
):
    """Explore is where somebody works out what they want; this is where it goes.

    Without it the only way to keep a filter worked out on screen was to open
    the pipeline builder and set the same conditions again from memory - which
    is the moment the two quietly stop matching.
    """
    steps = steps_from_query(
        columns=payload.columns,
        filters=payload.filters,
        sort_by=payload.sort_by,
        sort_desc=payload.sort_desc,
    )
    pipeline = service.create(
        name=payload.name,
        input_dataset_id=payload.dataset_id,
        steps=steps,
        description=payload.description,
    )
    audit.record(
        action="pipeline.create_from_query", resource_type="pipeline",
        resource_id=pipeline.id, actor=user,
        detail={"steps": [s.name for s in pipeline.steps]},
    )
    return _out(pipeline)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineOut)
def get_pipeline(pipeline_id: str, service: PipelineServiceDep):
    return _out(service.get(pipeline_id))


@router.get("/pipelines/{pipeline_id}/graph", summary="Nodes and edges for rendering")
def get_pipeline_graph(pipeline_id: str, service: PipelineServiceDep):
    return service.graph(pipeline_id)


@router.patch("/pipelines/{pipeline_id}", response_model=PipelineOut)
def update_pipeline(
    pipeline_id: str, payload: PipelineUpdate, service: PipelineServiceDep
):
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("steps") is not None:
        changes["steps"] = [dict(step) for step in changes["steps"]]
    return _out(service.update(pipeline_id, changes))


@router.delete("/pipelines/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pipeline(
    pipeline_id: str,
    service: PipelineServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
) -> None:
    service.delete(pipeline_id)
    audit.record(
        action="pipeline.delete", resource_type="pipeline",
        resource_id=pipeline_id, actor=user,
    )


@router.post(
    "/pipelines/{pipeline_id}/run",
    response_model=PipelineRunOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run every step in dependency order",
)
def run_pipeline(
    pipeline_id: str,
    payload: RunRequest,
    service: PipelineServiceDep,
    jobs: JobServiceDep,
    audit: AuditServiceDep,
    user: CurrentUser,
    background: bool = Query(
        False,
        description=(
            "Submit the run as a job and return immediately. A twelve-step "
            "pipeline does not belong inside an HTTP request."
        ),
    ),
):
    if background:
        job = jobs.submit(
            kind="pipeline_run",
            target_id=pipeline_id,
            parameters={"dataset_version_id": payload.dataset_version_id},
            requested_by=user.id,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"job_id": job.id, "status": job.status.value},
        )

    run = service.run(
        pipeline_id,
        dataset_version_id=payload.dataset_version_id,
        triggered_by=user.id,
    )
    audit.record(
        action="pipeline.run", resource_type="pipeline", resource_id=pipeline_id,
        actor=user, detail={"run_id": run.id, "status": run.status.value},
        outcome="succeeded" if run.status.value == "succeeded" else "failed",
    )
    return _run_out(run)


@router.get("/pipeline-runs", response_model=list[PipelineRunOut])
def list_pipeline_runs(
    service: PipelineServiceDep,
    pipeline_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    return [_run_out(r) for r in service.list_runs(pipeline_id=pipeline_id, limit=limit)]


@router.get("/pipeline-runs/{run_id}", response_model=PipelineRunOut)
def get_pipeline_run(run_id: str, service: PipelineServiceDep):
    return _run_out(service.get_run(run_id))


def _out(pipeline: Pipeline) -> PipelineOut:
    return PipelineOut(
        id=pipeline.id,
        name=pipeline.name,
        description=pipeline.description,
        input_dataset_id=pipeline.input_dataset_id,
        steps=[s.to_dict() for s in pipeline.steps],
        status=pipeline.status.value,
        tags=pipeline.tags,
        last_run_id=pipeline.last_run_id,
        last_run_status=pipeline.last_run_status,
        created_at=pipeline.created_at,
        updated_at=pipeline.updated_at,
        project_id=pipeline.project_id,
    )


def _run_out(run: PipelineRun) -> PipelineRunOut:
    return PipelineRunOut(
        id=run.id,
        pipeline_id=run.pipeline_id,
        status=run.status.value,
        input_dataset_version_id=run.input_dataset_version_id,
        definition_snapshot=run.definition_snapshot,
        execution_id=run.execution_id,
        step_runs=[s.to_dict() for s in run.step_runs],
        output_dataset_ids=run.output_dataset_ids,
        error=run.error,
        duration_seconds=run.duration_seconds,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
    )
