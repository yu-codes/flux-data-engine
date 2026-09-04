"""Pipelines: a graph of model executions over datasets.

Each step runs one Model. A step's input is either the pipeline's input dataset
or the output of an earlier step, so the steps form a directed acyclic graph.

Every provider in the platform consumes exactly one input table, so the graph
branches but does not merge: one step may feed several, but no step takes two
parents. Merging would need a join-shaped provider; when one exists, `inputs`
becomes a list and the executor's topological walk already handles it.

    Dataset ──▶ step A ──▶ step B ──▶ step C
                       └──▶ step D
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.errors import ValidationError
from app.shared.ids import new_id, utcnow


class PipelineStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    ARCHIVED = "archived"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)


@dataclass
class PipelineStep:
    """One computation in the graph.

    A step says what to run in one of two ways.

    Inline - a `provider` and a `configuration` - is the normal one. The step's
    configuration has no life outside the pipeline: nobody searches for it,
    versions it, or wants it in a list of models. Giving every step a
    ModelDefinition row meant creating exactly those things and then inventing
    a scope flag to hide them again.

    By `model_id` is the second: a step that runs something from the library on
    purpose, so that improving the model improves every pipeline that uses it.

    By `pipeline_id` is the third: a step that runs another pipeline. A
    pipeline is a runnable like any other, and the alternative to nesting is
    copying a shared five-step preparation into every pipeline that needs it -
    after which fixing it means finding all the copies.
    """

    name: str
    model_id: str | None = None
    pipeline_id: str | None = None
    provider: str | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    kind: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    #  None means "the pipeline's input dataset"; otherwise an earlier step name.
    input_from: str | None = None
    #  Extra inputs, wired by name to an earlier step: {"right": "load prices"}.
    #  A step with these is where the graph merges.
    inputs: dict[str, str] = field(default_factory=dict)
    #  Extra inputs that are datasets rather than steps: {"right": "ds_..."}.
    #  The usual join is against a reference table - prices, regions, a lookup -
    #  which is not derived from the pipeline's input and so cannot be reached
    #  by wiring steps together.
    input_datasets: dict[str, str] = field(default_factory=dict)
    #  Keep this step's output as a Dataset even though something downstream
    #  reads it. Off by default because a step in the middle of a run is
    #  working state; the ends of a pipeline are materialised automatically,
    #  since producing them is the reason the pipeline exists.
    materialise: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        named = [
            name
            for name, value in (
                ("model_id", self.model_id),
                ("provider", self.provider),
                ("pipeline_id", self.pipeline_id),
            )
            if value
        ]
        if not named:
            raise ValidationError(
                f"step '{self.name}' must name a provider, a model_id "
                f"or a pipeline_id"
            )
        if len(named) > 1:
            #  Two answers to "what does this step run" is not a preference to
            #  resolve quietly; whichever one lost would run for years.
            raise ValidationError(
                f"step '{self.name}' names {sorted(named)}; a step runs one thing"
            )

    @property
    def runs_library_model(self) -> bool:
        return bool(self.model_id)

    @property
    def runs_pipeline(self) -> bool:
        return bool(self.pipeline_id)

    @property
    def upstream(self) -> list[str]:
        """Every step this one reads from, in wiring order."""
        names = [self.input_from] if self.input_from else []
        names.extend(self.inputs.values())
        #  Order preserved, duplicates dropped: a step may legitimately read
        #  the same upstream twice under two names.
        return list(dict.fromkeys(n for n in names if n))

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "model_id": self.model_id,
            "pipeline_id": self.pipeline_id,
            "provider": self.provider,
            "configuration": self.configuration,
            "kind": self.kind,
            "parameters": self.parameters,
            "input_from": self.input_from,
            "inputs": self.inputs,
            "input_datasets": self.input_datasets,
            "materialise": self.materialise,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> PipelineStep:
        return cls(
            name=str(raw["name"]),
            model_id=raw.get("model_id") or None,
            pipeline_id=raw.get("pipeline_id") or None,
            provider=raw.get("provider") or None,
            configuration=raw.get("configuration") or {},
            kind=raw.get("kind"),
            parameters=raw.get("parameters") or {},
            input_from=raw.get("input_from"),
            inputs=raw.get("inputs") or {},
            input_datasets=raw.get("input_datasets") or {},
            #  Older rows predate the flag and every step materialised, so an
            #  absent value must keep meaning what it meant when it was written.
            materialise=bool(raw.get("materialise", False)),
            description=raw.get("description", "") or "",
        )


@dataclass
class Pipeline:
    name: str
    input_dataset_id: str
    steps: list[PipelineStep] = field(default_factory=list)
    description: str = ""
    status: PipelineStatus = PipelineStatus.DRAFT
    tags: list[str] = field(default_factory=list)
    last_run_id: str | None = None
    last_run_status: str | None = None
    id: str = field(default_factory=lambda: new_id("pipe"))
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

    def step(self, name: str) -> PipelineStep | None:
        return next((s for s in self.steps if s.name == name), None)

    @property
    def terminal_steps(self) -> list[PipelineStep]:
        """Steps nothing else consumes — the pipeline's outputs."""
        #  Every edge, so a step feeding only a join is not mistaken for a
        #  terminal one and published as an output nobody asked for.
        consumed = {name for s in self.steps for name in s.upstream}
        return [s for s in self.steps if s.name not in consumed]


@dataclass
class StepRun:
    """What happened to one step during one run."""

    step_name: str
    model_id: str
    order: int
    status: RunStatus = RunStatus.PENDING
    execution_id: str | None = None
    #  The run this step delegated to, when the step is another pipeline.
    #  Without it a nested run is an orphan: something clearly happened, and
    #  nothing says what asked for it.
    pipeline_run_id: str | None = None
    result_id: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    row_count: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return {
            "step_name": self.step_name,
            "model_id": self.model_id,
            "order": self.order,
            "status": self.status.value,
            "execution_id": self.execution_id,
            "pipeline_run_id": self.pipeline_run_id,
            "result_id": self.result_id,
            "dataset_id": self.dataset_id,
            "dataset_version_id": self.dataset_version_id,
            "row_count": self.row_count,
            "metrics": self.metrics,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> StepRun:
        return cls(
            step_name=raw["step_name"],
            model_id=raw["model_id"],
            order=int(raw.get("order", 0)),
            status=RunStatus(raw.get("status", "pending")),
            execution_id=raw.get("execution_id"),
            pipeline_run_id=raw.get("pipeline_run_id"),
            result_id=raw.get("result_id"),
            dataset_id=raw.get("dataset_id"),
            dataset_version_id=raw.get("dataset_version_id"),
            row_count=raw.get("row_count"),
            metrics=raw.get("metrics") or {},
            error=raw.get("error"),
            duration_seconds=raw.get("duration_seconds"),
        )


@dataclass
class PipelineRun:
    pipeline_id: str
    status: RunStatus = RunStatus.PENDING
    input_dataset_version_id: str | None = None
    #  The execution that asked for this run, when one did. A pipeline is a
    #  runnable now, so a run can be the work behind an ordinary Execution -
    #  and the two need to be able to find each other afterwards.
    execution_id: str | None = None
    #  What the pipeline was when this run started.
    #
    #  A Model fixed this problem already: `definition_snapshot` is why an
    #  execution can say what it ran even after the model was edited. A
    #  pipeline run could not - edit a step and every past run silently starts
    #  describing itself with the new steps, which is the difference between a
    #  record and a guess.
    definition_snapshot: dict[str, Any] = field(default_factory=dict)
    step_runs: list[StepRun] = field(default_factory=list)
    output_dataset_ids: list[str] = field(default_factory=list)
    error: str | None = None
    triggered_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("prun"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def duration_seconds(self) -> float | None:
        if not self.started_at or not self.finished_at:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    @property
    def succeeded_steps(self) -> int:
        return sum(1 for run in self.step_runs if run.status is RunStatus.SUCCEEDED)


def validate_steps(steps: list[PipelineStep]) -> list[PipelineStep]:
    """Check names, references and acyclicity, and return execution order.

    Raises `ValidationError` with a message naming the offending step, because
    this is the error a user is most likely to hit while building a pipeline.
    """
    if not steps:
        raise ValidationError("a pipeline needs at least one step")

    names = [step.name for step in steps]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise ValidationError(f"duplicate step name(s): {sorted(duplicates)}")
    for step in steps:
        if not step.name.strip():
            raise ValidationError("every step needs a name")
        for upstream in step.upstream:
            if upstream not in names:
                raise ValidationError(
                    f"step '{step.name}' reads from '{upstream}', "
                    f"which is not a step in this pipeline"
                )
            if upstream == step.name:
                raise ValidationError(f"step '{step.name}' cannot read from itself")

    return topological_order(steps)


def waves(ordered: list[PipelineStep]) -> list[list[PipelineStep]]:
    """Group steps into rounds that may run at the same time.

    A step belongs to the round after the last of the steps it reads from, so
    everything in one round has its inputs already and waits for nothing else
    in that round. Order within a round is the topological order it came in,
    so a run with parallelism switched off behaves exactly as it always did.

    Takes an ordered list rather than sorting again: whether the graph is
    acyclic is `validate_steps`'s question, asked once.
    """
    depth: dict[str, int] = {}
    for step in ordered:
        depth[step.name] = (
            max((depth.get(name, 0) for name in step.upstream), default=-1) + 1
        )
    grouped: dict[int, list[PipelineStep]] = {}
    for step in ordered:
        grouped.setdefault(depth[step.name], []).append(step)
    return [grouped[level] for level in sorted(grouped)]


def topological_order(steps: list[PipelineStep]) -> list[PipelineStep]:
    """Order steps so every step comes after the one it reads from."""
    by_name = {step.name: step for step in steps}
    ordered: list[PipelineStep] = []
    state: dict[str, str] = {}

    def visit(step: PipelineStep, trail: list[str]) -> None:
        mark = state.get(step.name)
        if mark == "done":
            return
        if mark == "visiting":
            cycle = " -> ".join([*trail, step.name])
            raise ValidationError(f"the steps form a cycle: {cycle}")
        state[step.name] = "visiting"
        #  Every parent, not just the first: a merging step must come after
        #  all of the steps it reads from, or it runs on half its input.
        for upstream in step.upstream:
            visit(by_name[upstream], [*trail, step.name])
        state[step.name] = "done"
        ordered.append(step)

    for step in steps:
        visit(step, [])
    return ordered
