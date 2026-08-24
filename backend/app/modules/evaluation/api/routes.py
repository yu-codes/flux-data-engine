"""Experiments and evaluations API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse
from pydantic import Field

from app.api.deps import (
    EvaluationServiceDep,
    ExecutionServiceDep,
    ExperimentServiceDep,
    JobServiceDep,
)
from app.api.schema_base import ApiModel

router = APIRouter(tags=["evaluation"])


class TrialIn(ApiModel):
    """One thing being compared.

    `model_id` is the familiar spelling; `target_id` + `target_type` is the
    general one, and the only way to put a pipeline in an experiment. Exactly
    one is needed.
    """

    model_id: str | None = None
    target_id: str | None = None
    target_type: str = "model"
    label: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    model_version_id: str | None = None
    kind: str | None = None

    def as_trial(self) -> dict[str, Any]:
        target = self.target_id or self.model_id
        if not target:
            raise ValueError("a trial needs a model_id or a target_id")
        return {
            "target_id": target,
            "target_type": "model" if self.model_id else self.target_type,
            "label": self.label,
            "parameters": self.parameters,
            "model_version_id": self.model_version_id,
            "kind": self.kind,
        }


class TrialOut(ApiModel):
    target_id: str
    target_type: str
    #  Empty for a pipeline: "which model" has an honest answer either way.
    model_id: str
    label: str
    parameters: dict[str, Any]
    model_version_id: str | None
    kind: str | None


class ExperimentCreate(ApiModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    objective: str = ""
    primary_direction: str = "higher"
    primary_metric: str = ""
    #  What every trial runs against: holding the data constant is what makes
    #  the trials comparable.
    dataset_version_id: str | None = None
    trials: list[TrialIn] | None = None
    #  Accepted so an existing caller keeps working; each id becomes a trial
    #  with default parameters, which is what it always meant.
    model_ids: list[str] = Field(default_factory=list)


class ExperimentUpdate(ApiModel):
    name: str | None = None
    description: str | None = None
    objective: str | None = None
    primary_direction: str | None = None
    primary_metric: str | None = None
    dataset_version_id: str | None = None
    trials: list[TrialIn] | None = None


class ExperimentOut(ApiModel):
    id: str
    name: str
    description: str
    objective: str
    primary_direction: str
    primary_metric: str
    dataset_version_id: str | None
    trials: list[TrialOut]
    model_ids: list[str]
    execution_ids: list[str]
    created_at: datetime


class CompareIn(ApiModel):
    experiment_ids: list[str] = Field(min_length=1, max_length=8)
    #  Optional: otherwise the shared primary metric is used.
    metric: str | None = None
    #  Every run rather than the latest per trial, for looking at drift.
    include_history: bool = False


class EvaluationCreate(ApiModel):
    execution_id: str
    metrics: dict[str, Any]
    target: dict[str, Any] = Field(default_factory=dict)
    model_id: str | None = None
    experiment_id: str | None = None
    notes: str = ""


class EvaluationOut(ApiModel):
    id: str
    execution_id: str
    model_id: str | None
    experiment_id: str | None
    metrics: dict[str, Any]
    target: dict[str, Any]
    passed: bool | None
    notes: str
    created_at: datetime


class LeaderboardRow(ApiModel):
    #  A row is a trial, not a model: a sweep of one model at three values of k
    #  is three rows, and keying on the model collapsed it to one.
    trial: str
    #  What this trial compared, and what kind of runnable it was. The model_*
    #  fields describe it either way - for a pipeline trial they carry the
    #  pipeline's name and step count, and model_id is empty rather than
    #  borrowed.
    target_id: str
    target_type: str
    model_id: str
    model_name: str
    model_type: str
    provider: str
    evaluation_id: str | None
    execution_id: str | None
    metrics: dict[str, Any]
    primary_value: float | None
    passed: bool | None
    evaluated_at: datetime | None


class LeaderboardOut(ApiModel):
    experiment_id: str
    name: str
    objective: str
    primary_direction: str
    primary_metric: str
    metric_names: list[str]
    rows: list[LeaderboardRow]


# --------------------------------------------------------------------------
# experiments
# --------------------------------------------------------------------------
@router.get("/experiments", response_model=list[ExperimentOut])
def list_experiments(service: ExperimentServiceDep):
    return [_experiment_out(e) for e in service.list()]


@router.post(
    "/experiments", response_model=ExperimentOut, status_code=status.HTTP_201_CREATED
)
def create_experiment(payload: ExperimentCreate, service: ExperimentServiceDep):
    return _experiment_out(
        service.create(
            name=payload.name,
            description=payload.description,
            objective=payload.objective,
            primary_metric=payload.primary_metric,
            primary_direction=payload.primary_direction,
            dataset_version_id=payload.dataset_version_id,
            trials=(
                [trial.as_trial() for trial in payload.trials]
                if payload.trials
                else None
            ),
            model_ids=payload.model_ids,
        )
    )


@router.get("/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: str, service: ExperimentServiceDep):
    return _experiment_out(service.get(experiment_id))


@router.patch("/experiments/{experiment_id}", response_model=ExperimentOut)
def update_experiment(
    experiment_id: str, payload: ExperimentUpdate, service: ExperimentServiceDep
):
    changes = payload.model_dump(exclude_unset=True)
    if "trials" in changes and changes["trials"] is not None:
        changes["trials"] = [dict(trial) for trial in changes["trials"]]
    return _experiment_out(service.update(experiment_id, changes))


@router.get(
    "/experiments/{experiment_id}/check",
    summary="Whether this experiment can run, trial by trial",
)
def check_experiment(experiment_id: str, service: ExperimentServiceDep):
    """Validate before anything executes.

    Every trial is checked against its model's parameter contract and the
    experiment's dataset, so a comparison never starts and then fails halfway —
    partial results look like an answer.
    """
    return service.check(experiment_id)


@router.post(
    "/experiments/{experiment_id}/run",
    response_model=ExperimentOut,
    summary="Run every trial as one act",
)
def run_experiment(
    experiment_id: str,
    service: ExperimentServiceDep,
    executions: ExecutionServiceDep,
    jobs: JobServiceDep,
    background: bool = Query(
        False, description="Submit the run as a job and return immediately."
    ),
):
    """The unit of execution is the experiment, not the model.

    Submitting trials one at a time is how a comparison ends up missing an arm
    that nobody notices, so all of them go together or none do.

    An experiment of several trials over a real dataset takes as long as the
    trials do, which is why it can be asked for in the background.
    """
    if background:
        job = jobs.submit(kind="experiment_run", target_id=experiment_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"job_id": job.id, "status": job.status.value},
        )
    return _experiment_out(service.run(experiment_id, executions))


@router.delete("/experiments/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experiment(experiment_id: str, service: ExperimentServiceDep) -> None:
    service.delete(experiment_id)


@router.get(
    "/experiments/{experiment_id}/leaderboard",
    response_model=LeaderboardOut,
    summary="Where each trial of this experiment stands",
)
def experiment_leaderboard(experiment_id: str, service: ExperimentServiceDep):
    return LeaderboardOut(**service.leaderboard(experiment_id))


@router.post(
    "/experiments/compare",
    summary="Compare several experiments on whatever metrics they share",
)
def compare_experiments(payload: CompareIn, service: ExperimentServiceDep):
    return service.compare(
        payload.experiment_ids,
        metric=payload.metric,
        include_history=payload.include_history,
    )


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------
@router.get("/evaluations", response_model=list[EvaluationOut])
def list_evaluations(
    service: EvaluationServiceDep,
    model_id: str | None = Query(None),
    experiment_id: str | None = Query(None),
):
    filters = {}
    if model_id:
        filters["model_id"] = model_id
    if experiment_id:
        filters["experiment_id"] = experiment_id
    return [_evaluation_out(e) for e in service.list(**filters)]


@router.post(
    "/evaluations", response_model=EvaluationOut, status_code=status.HTTP_201_CREATED
)
def create_evaluation(payload: EvaluationCreate, service: EvaluationServiceDep):
    return _evaluation_out(
        service.record(
            execution_id=payload.execution_id,
            metrics=payload.metrics,
            target=payload.target,
            model_id=payload.model_id,
            experiment_id=payload.experiment_id,
            notes=payload.notes,
        )
    )


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------


def _experiment_out(experiment) -> ExperimentOut:
    return ExperimentOut(
        id=experiment.id,
        name=experiment.name,
        description=experiment.description,
        objective=experiment.objective,
        primary_metric=experiment.primary_metric,
        primary_direction=experiment.primary_direction,
        dataset_version_id=experiment.dataset_version_id,
        trials=[TrialOut(**trial.to_dict()) for trial in experiment.trials],
        model_ids=experiment.model_ids,
        execution_ids=experiment.execution_ids,
        created_at=experiment.created_at,
    )


def _evaluation_out(evaluation) -> EvaluationOut:
    return EvaluationOut(
        id=evaluation.id,
        execution_id=evaluation.execution_id,
        model_id=evaluation.model_id,
        experiment_id=evaluation.experiment_id,
        metrics=evaluation.metrics,
        target=evaluation.target,
        passed=evaluation.passed,
        notes=evaluation.notes,
        created_at=evaluation.created_at,
    )
