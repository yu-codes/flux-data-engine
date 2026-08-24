"""Serving: calling a model and getting the answer back.

A separate router from `/executions` because it needs a different guard, and
it needs a different guard because it is a different kind of act. Submitting an
execution changes the platform's state - a row, a result, sometimes a dataset.
Invoking computes an answer and changes nothing, so it is governed by the
permission that covers reading rather than the one that covers running.

Without that distinction a read-only integration key could not do the single
thing it exists to do.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from app.api.deps import ExecutionServiceDep
from app.api.schema_base import ApiModel

router = APIRouter(tags=["serving"])


class InvokeIn(ApiModel):
    kind: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    dataset_version_id: str | None = None
    #  Either identifies the input. A dataset id runs against its current
    #  version, which is what a caller holding a dataset almost always means.
    dataset_id: str | None = None


@router.post(
    "/models/{model_id}/invoke",
    summary="Run a model and return the answer, without recording anything",
)
def invoke_model(model_id: str, payload: InvokeIn, service: ExecutionServiceDep):
    """Call a model the way another system would.

    `POST /executions` is the batch verb: it records what ran, keeps the
    result, and can materialise a dataset, which is what makes a run auditable
    months later. This is the online one - same contracts, same plugin, same
    published version, but no rows written and no dataset created, because a
    caller that wants an answer in fifty milliseconds is not asking for an
    audit trail.

    Reachable with an API key, so integrating does not mean handing a person's
    password to a service.
    """
    return service.invoke(
        model_id=model_id,
        kind=payload.kind,
        input_payload=payload.input,
        parameters=payload.parameters,
        dataset_version_id=payload.dataset_version_id,
        dataset_id=payload.dataset_id,
    )
