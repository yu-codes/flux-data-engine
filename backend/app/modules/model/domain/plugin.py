"""The plugin contract every model provider implements.

    ModelPlugin
        |
        +-- ExecutableModelPlugin   (formula, rule, simulation, analog, ...)
        |
        +-- TrainableModelPlugin    (adds train(); scikit-learn, XGBoost, ...)

Executable is the baseline. Trainable is an *extra* capability, not a
prerequisite - a formula model is a perfectly complete model with no training
step at all.

Framework-specific imports (sklearn, xgboost, torch) belong in the plugin
implementations under ``app/plugins``, never in this domain module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from app.shared.contracts import Contract, FieldSpec, ValidationResult
from app.shared.payloads import ResultPayload
from app.shared.tabular import Table

from ..domain.entities import ModelDefinition, ModelType, RuntimeKind


class ExecutionKind(str, Enum):
    """What an execution is *doing*. Prediction is one of several kinds."""

    TRAINING = "training"
    PREDICTION = "prediction"
    SIMULATION = "simulation"
    OPTIMIZATION = "optimization"
    CALCULATION = "calculation"
    EVALUATION = "evaluation"
    TRANSFORMATION = "transformation"


@dataclass
class ExecutionInput:
    """The data side of an execution.

    Either a materialised Table (from a dataset version) or inline records.
    Plugins should read ``table`` when they are dataset-shaped and ``record``
    when they take a single parameter object.

    ``inputs`` is for the few providers that read more than one table - a join,
    most obviously. `table` stays the primary one so that every single-input
    provider is unaffected: the plural is available to those that ask for it
    and invisible to those that do not.
    """

    table: Table | None = None
    record: dict[str, Any] = field(default_factory=dict)
    dataset_version_id: str | None = None
    dataset_id: str | None = None
    inputs: dict[str, Table] = field(default_factory=dict)

    @property
    def has_table(self) -> bool:
        return self.table is not None and self.table.num_rows > 0

    def named(self, name: str) -> Table | None:
        """One of several inputs, by the name the caller wired it under."""
        return self.inputs.get(name)

    def rows(self) -> list[dict]:
        return self.table.to_rows() if self.table is not None else []


@dataclass
class ExecutionContext:
    """Everything a plugin is given, and nothing else.

    Plugins receive no session, no repository and no HTTP request - they are
    pure computational units over (input, parameters, context).
    """

    execution_id: str
    kind: ExecutionKind
    definition: ModelDefinition
    input: ExecutionInput
    parameters: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    #  Artifact of the model version being executed, if any (trained weights).
    artifact_path: str | None = None
    #  Directory the plugin may write artifacts into.
    workdir: str | None = None
    logs: list[str] = field(default_factory=list)
    #  Asked before each expensive stage of a long computation. A plugin that
    #  never checks still works; one that does can stop early and leave the
    #  execution cancelled rather than being killed mid-write.
    #
    #  Still a pure function: this is a callable the caller supplies, not a
    #  session, a repository or a request.
    should_cancel: Callable[[], bool] = lambda: False
    #  When this execution is expected to be finished. A provider that searches
    #  an open-ended space - a grid, a simulation - checks it and returns the
    #  best answer it has rather than running until something kills it.
    deadline: float | None = None
    #  The datasets the provider declared, already read. Keyed by the key
    #  the provider chose, so it asks for what it named rather than for
    #  whatever happened to be resolved.
    datasets: dict[str, Any] = field(default_factory=dict)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def out_of_time(self) -> bool:
        """Whether this execution has run past the time it was given."""
        if self.deadline is None:
            return False
        return time.monotonic() >= self.deadline

    def should_stop(self) -> bool:
        """Cancelled, or out of time. What a long loop should be asking."""
        return self.cancelled() or self.out_of_time()

    def cancelled(self) -> bool:
        """Whether the caller has asked this execution to stop."""
        try:
            return bool(self.should_cancel())
        except Exception:
            #  A broken callback must never stop the work it was watching.
            return False


@dataclass
class ExecutionOutcome:
    """What a plugin returns: a Result payload plus metrics and logs."""

    payload: ResultPayload
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)


@dataclass
class TrainingOutcome:
    """A training execution produces a new immutable Model Version."""

    artifact_path: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    payload: ResultPayload | None = None
    logs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RequiredDataset:
    """A dataset a provider needs in order to run.

    Resolved by name rather than by id, because a provider cannot know the ids
    in a particular installation - and because "the typhoon catalogue" is the
    thing it depends on, not one specific version of it.
    """

    key: str
    name: str
    description: str = ""
    #  A provider that can degrade gracefully says so; one that cannot is
    #  reported as unrunnable before anybody presses Run.
    required: bool = True

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "required": self.required,
        }


@dataclass
class PluginDescriptor:
    """Self-description a provider publishes to the Model Library UI."""

    key: str
    name: str
    model_type: ModelType
    runtime: RuntimeKind
    description: str = ""
    trainable: bool = False
    supported_kinds: tuple[ExecutionKind, ...] = (ExecutionKind.CALCULATION,)
    parameter_contract: Contract = field(default_factory=Contract)
    input_contract: Contract = field(default_factory=Contract)
    output_contract: Contract = field(default_factory=Contract)
    configuration_contract: Contract = field(default_factory=Contract)
    examples: list[dict[str, Any]] = field(default_factory=list)
    #  Datasets this provider reads. The platform resolves them and hands
    #  them over, so the plugin still touches no repository and no session.
    required_datasets: tuple[RequiredDataset, ...] = ()

    #  What this provider is, at the moment it answered. Reproducibility was
    #  guaranteed only as far as the definition: the same snapshot run after
    #  the provider changed - a new scikit-learn, a corrected formula - gives a
    #  different answer and nothing in the record said why. A provider that
    #  wraps a library should report that library's version.
    version: str = "1"
    #  How long this provider needs, when the platform's single default is the
    #  wrong number for it. A formula answers in milliseconds; a leave-one-out
    #  backtest takes minutes. One timeout for both is either too tight to be
    #  safe or too loose to be useful.
    timeout_seconds: int | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            #  What answered, and how long it may take. Both are read by the
            #  platform, not decoration: the version travels into an
            #  execution's lineage and the timeout becomes its deadline.
            "version": self.version,
            "timeout_seconds": self.timeout_seconds,
            "model_type": self.model_type.value,
            "runtime": self.runtime.value,
            "description": self.description,
            "trainable": self.trainable,
            "supported_kinds": [k.value for k in self.supported_kinds],
            "required_datasets": [d.to_dict() for d in self.required_datasets],
            "parameter_contract": self.parameter_contract.to_dict(),
            "input_contract": self.input_contract.to_dict(),
            "output_contract": self.output_contract.to_dict(),
            "configuration_contract": self.configuration_contract.to_dict(),
            "examples": self.examples,
        }


@runtime_checkable
class ModelPlugin(Protocol):
    """Minimum surface of a model provider."""

    def describe(self) -> PluginDescriptor:
        """Static self-description used for discovery and UI rendering."""

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        """Check that a model definition is coherent for this provider."""

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        """Run the model and return a Result payload."""


@runtime_checkable
class DatasetAwareModelPlugin(ModelPlugin, Protocol):
    """A provider that can check a dataset before anything runs against it.

    A contract describes the *shape* of an input, which is enough for a model
    whose fields are declared. It is not enough for one that names its columns
    in configuration — a curve fit reading `x` and `y`, a transform reading
    `options.column`. Nothing outside the provider knows those keys, and
    teaching the platform to guess at them is exactly the case-specific code the
    plugin boundary exists to prevent.

    Optional. A provider that does not implement it is checked as before, and
    the experiment says plainly that it could not verify the columns.
    """

    def check_dataset(
        self, definition: ModelDefinition, schema_fields: list[FieldSpec]
    ) -> ValidationResult:
        """Whether this dataset can satisfy this model's configuration."""
        ...


@runtime_checkable
class TrainableModelPlugin(ModelPlugin, Protocol):
    """A provider that can additionally *fit* a model version from data."""

    def train(self, context: ExecutionContext) -> TrainingOutcome:
        """Fit from the execution input and emit an artifact + metrics."""


def is_trainable(plugin: Any) -> bool:
    """Duck-typed capability check - Protocol runtime checks ignore methods."""
    return callable(getattr(plugin, "train", None))
