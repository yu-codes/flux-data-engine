"""Serving: calling a pipeline and getting the answer back.

The same act as invoking a model, so it lives under the same guard rather than
under the one that governs editing pipelines: it computes an answer and
records nothing. A pipeline is a runnable, and a runnable that could be run
but not called would be a runnable in name only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from app.api.deps import PipelineServiceDep
from app.api.schema_base import ApiModel

router = APIRouter(tags=["serving"])


class InvokePipelineIn(ApiModel):
    parameters: dict[str, Any] = Field(default_factory=dict)
    dataset_version_id: str | None = None
    #  Either identifies the stored input. A dataset id runs against its
    #  current version, which is what a caller holding a dataset usually means.
    dataset_id: str | None = None
    #  Data the caller brings, which is what makes a pipeline callable rather
    #  than merely schedulable. Takes precedence over the stored input.
    rows: list[dict[str, Any]] | None = Field(
        default=None,
        description="run the pipeline over these rows instead of a stored dataset",
    )


@router.post(
    "/pipelines/{pipeline_id}/invoke",
    summary="Run a pipeline and return the answer, without recording anything",
)
def invoke_pipeline(
    pipeline_id: str, payload: InvokePipelineIn, service: PipelineServiceDep
):
    """Apply a pipeline to data and get its output back.

    `POST /pipelines/{id}/run` is the recorded verb: a PipelineRun, an
    Execution per step, results and datasets - which is what makes a run
    reviewable months later. This is the online one: same steps, same
    providers, same order, nothing written.
    """
    return service.invoke(
        pipeline_id,
        dataset_version_id=payload.dataset_version_id,
        dataset_id=payload.dataset_id,
        rows=payload.rows,
        parameters=payload.parameters,
    )
