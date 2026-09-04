"""Results API: read execution outputs and promote them to datasets."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from pydantic import Field

from app.api.deps import ResultServiceDep
from app.api.schema_base import ApiModel

from ..domain.entities import Result, ResultKind

router = APIRouter(tags=["results"])


class ResultOut(ApiModel):
    id: str
    execution_id: str
    kind: str
    summary: dict[str, Any]
    metrics: dict[str, Any]
    dataset_id: str | None
    dataset_version_id: str | None
    artifact_uri: str | None
    row_count: int | None
    is_materialised: bool
    created_at: datetime
    #  Where this is filed. Null means shared: it shows under every project
    #  rather than none, which is what the library relies on.
    project_id: str | None = None


class MaterialiseRequest(ApiModel):
    dataset_name: str = Field(min_length=1, max_length=255)


@router.get("/result-kinds", summary="Shapes a result can take")
def list_result_kinds():
    return {"kinds": [k.value for k in ResultKind]}


@router.get("/results", response_model=list[ResultOut])
def list_results(
    service: ResultServiceDep,
    kind: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    filters: dict[str, Any] = {"limit": limit}
    if kind:
        filters["kind"] = kind
    return [_out(r) for r in service.list(**filters)]


@router.get("/results/{result_id}", response_model=ResultOut)
def get_result(result_id: str, service: ResultServiceDep):
    return _out(service.get(result_id))


@router.get("/results/{result_id}/payload", summary="The substance of the result")
def get_result_payload(
    result_id: str, service: ResultServiceDep, limit: int = Query(200, ge=1, le=5000)
):
    return {"result_id": result_id, "payload": service.read_payload(result_id, limit=limit)}


@router.get("/executions/{execution_id}/result", response_model=ResultOut | None)
def get_result_for_execution(execution_id: str, service: ResultServiceDep):
    result = service.for_execution(execution_id)
    return _out(result) if result else None


@router.post("/results/{result_id}/materialise", status_code=status.HTTP_201_CREATED)
def materialise_result(
    result_id: str, payload: MaterialiseRequest, service: ResultServiceDep
):
    return service.materialise(result_id, payload.dataset_name)


@router.delete("/results/{result_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_result(result_id: str, service: ResultServiceDep) -> None:
    service.delete(result_id)


def _out(result: Result) -> ResultOut:
    return ResultOut(
        id=result.id,
        execution_id=result.execution_id,
        kind=result.kind.value,
        summary=result.summary,
        metrics=result.metrics,
        dataset_id=result.dataset_id,
        dataset_version_id=result.dataset_version_id,
        artifact_uri=result.artifact_uri,
        row_count=result.row_count,
        is_materialised=result.is_materialised,
        created_at=result.created_at,
        project_id=result.project_id,
    )
