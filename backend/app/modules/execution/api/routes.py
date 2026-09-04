"""Execution API: submit runs of any kind and inspect their state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.api.deps import ExecutionServiceDep
from app.api.schema_base import ApiModel

from ..domain.entities import Execution, ExecutionKind, ExecutionStatus

router = APIRouter(tags=["executions"])


class ExecutionCreate(ApiModel):
    #  Either names what to run. `model_id` is the familiar spelling and
    #  `target_id` + `target_type` is the general one; exactly one is needed.
    model_id: str | None = None
    target_id: str | None = None
    target_type: str = "model"
    kind: str | None = Field(
        default=None,
        description="training | prediction | simulation | optimization | "
                    "calculation | evaluation | transformation",
    )
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    model_version_id: str | None = None
    experiment_id: str | None = None


class ExecutionOut(ApiModel):
    id: str
    #  What ran, and what kind of runnable it was. Absent when the execution
    #  ran an inline definition rather than anything stored - a pipeline step,
    #  for instance. `model_id` is kept beside them because "which model" is
    #  still the common question, and answering it with null for a pipeline is
    #  more useful than making every caller branch on the kind.
    target_id: str | None
    target_type: str
    model_id: str | None
    definition_snapshot: dict[str, Any] = Field(default_factory=dict)
    model_version_id: str | None
    kind: str
    status: str
    runtime: str
    dataset_version_id: str | None
    parameters: dict[str, Any]
    context: dict[str, Any]
    metrics: dict[str, Any]
    lineage: dict[str, Any]
    logs: list[str]
    error: str | None
    result_id: str | None
    produced_model_version_id: str | None
    experiment_id: str | None
    duration_seconds: float | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    #  Where this is filed. Null means shared: it shows under every project
    #  rather than none, which is what the library relies on.
    project_id: str | None = None


@router.get("/execution-kinds", summary="Every kind of execution the platform runs")
def list_execution_kinds():
    return {
        "kinds": [k.value for k in ExecutionKind],
        "statuses": [s.value for s in ExecutionStatus],
    }


@router.get("/executions", response_model=list[ExecutionOut])
def list_executions(
    service: ExecutionServiceDep,
    model_id: str | None = Query(None),
    target_id: str | None = Query(None),
    target_type: str | None = Query(None),
    kind: str | None = Query(None),
    execution_status: str | None = Query(None, alias="status"),
    experiment_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    filters: dict[str, Any] = {"limit": limit}
    if model_id:
        filters["model_id"] = model_id
    if target_id:
        filters["target_id"] = target_id
    if target_type:
        filters["target_type"] = target_type
    if kind:
        filters["kind"] = kind
    if execution_status:
        filters["status"] = execution_status
    if experiment_id:
        filters["experiment_id"] = experiment_id
    return [_out(e) for e in service.list(**filters)]


@router.post(
    "/executions", response_model=ExecutionOut, status_code=status.HTTP_201_CREATED
)
def submit_execution(payload: ExecutionCreate, service: ExecutionServiceDep):
    execution = service.submit(
        model_id=payload.model_id or (
            payload.target_id if payload.target_type == "model" else None
        ),
        pipeline_id=(
            payload.target_id if payload.target_type == "pipeline" else None
        ),
        kind=payload.kind,
        dataset_id=payload.dataset_id,
        dataset_version_id=payload.dataset_version_id,
        input_payload=payload.input,
        parameters=payload.parameters,
        context=payload.context,
        model_version_id=payload.model_version_id,
        experiment_id=payload.experiment_id,
    )
    return _out(execution)


@router.get("/executions/{execution_id}", response_model=ExecutionOut)
def get_execution(execution_id: str, service: ExecutionServiceDep):
    return _out(service.get(execution_id))


@router.post("/executions/{execution_id}/cancel", response_model=ExecutionOut)
def cancel_execution(execution_id: str, service: ExecutionServiceDep):
    return _out(service.cancel(execution_id))


def _out(execution: Execution) -> ExecutionOut:
    return ExecutionOut(
        id=execution.id,
        target_id=execution.target_id,
        target_type=execution.target_type.value,
        model_id=execution.model_id,
        definition_snapshot=execution.definition_snapshot,
        model_version_id=execution.model_version_id,
        kind=execution.kind.value,
        status=execution.status.value,
        runtime=execution.runtime,
        dataset_version_id=execution.dataset_version_id,
        parameters=execution.parameters,
        context=execution.context,
        metrics=execution.metrics,
        lineage=execution.lineage,
        logs=execution.logs,
        error=execution.error,
        result_id=execution.result_id,
        produced_model_version_id=execution.produced_model_version_id,
        experiment_id=execution.experiment_id,
        duration_seconds=execution.duration_seconds,
        created_at=execution.created_at,
        started_at=execution.started_at,
        finished_at=execution.finished_at,
        project_id=execution.project_id,
    )
