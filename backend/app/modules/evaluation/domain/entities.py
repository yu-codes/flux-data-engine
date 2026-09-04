"""What is being compared, and what it scored."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.shared.ids import new_id, utcnow


@dataclass
class ExperimentTrial:
    """One thing being compared: a runnable, configured a particular way.

    A comparison is only meaningful when everything but the variable under test
    is held constant, so the dataset lives on the Experiment and the trial
    carries what differs — which runnable, and with what parameters.

    Pinning `model_version_id` is what makes a comparison still mean something
    next month: without it, re-running the experiment silently compares whatever
    the models have become.

    `target_type` is what lets a trial be a pipeline. "Which of these two ways
    of preparing the data is better" is the same question as "which of these
    two models is better", and for a long time the platform could only ask one
    of them.
    """

    target_id: str
    label: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    model_version_id: str | None = None
    kind: str | None = None
    target_type: str = "model"

    @property
    def model_id(self) -> str:
        """The model this trial runs, when it runs a model.

        Read-only: callers that mean "what is being compared" say `target_id`,
        which is the distinction that lets a pipeline into an experiment.
        """
        return self.target_id if self.target_type == "model" else ""

    def display_name(self, fallback: str = "") -> str:
        return self.label or fallback or self.target_id

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "target_type": self.target_type,
            #  Kept beside them because "which model" is still what most
            #  readers of a trial want, and answering "" for a pipeline is
            #  clearer than making each of them branch.
            "model_id": self.model_id,
            "label": self.label,
            "parameters": self.parameters,
            "model_version_id": self.model_version_id,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> ExperimentTrial:
        #  Rows written before trials could be anything but a model say only
        #  `model_id`; they are model trials, which is what the default says.
        return cls(
            target_id=str(raw.get("target_id") or raw["model_id"]),
            target_type=str(raw.get("target_type") or "model"),
            label=raw.get("label", "") or "",
            parameters=raw.get("parameters") or {},
            model_version_id=raw.get("model_version_id"),
            kind=raw.get("kind"),
        )


@dataclass
class Experiment:
    """A tracked comparison of models, parameters, data or methods.

    Not ML-specific: an experiment may hold a formula, a rule model and an
    XGBoost model side by side and compare them on cost, runtime or error.
    """

    name: str
    description: str = ""
    objective: str = ""                       # e.g. "maximise category accuracy"
    primary_metric: str = ""
    #  Which way is better. Accuracy is higher, RMSE is lower, and the
    #  leaderboard used to assume the first for both - so an experiment ranked
    #  by error put the worst trial at the top and labelled it the leader.
    #  Declared rather than guessed from the metric's name: a name is a string
    #  coincidence, and two providers can spell the same idea differently.
    primary_direction: str = "higher"
    #  What every trial runs against. Holding the data constant is what makes
    #  the trials comparable at all.
    dataset_version_id: str | None = None
    trials: list[ExperimentTrial] = field(default_factory=list)
    execution_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("exp"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)

    @property
    def model_ids(self) -> list[str]:
        """The distinct models under comparison, in trial order."""
        seen: list[str] = []
        for trial in self.trials:
            if trial.model_id not in seen:
                seen.append(trial.model_id)
        return seen


@dataclass
class Evaluation:
    """Measures whether an execution's Result met a stated objective.

    Metric names are open on purpose: accuracy and RMSE for ML, absolute error
    for a formula, objective value and constraint violation for optimisation,
    scenario deviation for simulation.
    """

    execution_id: str
    metrics: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    passed: bool | None = None
    notes: str = ""
    model_id: str | None = None
    experiment_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    id: str = field(default_factory=lambda: new_id("eval"))
    created_at: datetime = field(default_factory=utcnow)
