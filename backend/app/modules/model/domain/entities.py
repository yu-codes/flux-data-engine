"""Model domain.

A Model is NOT a machine-learning model. It is any versioned, describable and
executable computational unit that turns inputs, parameters and context into
outputs. Machine learning is one provider among several; training is optional.

    Output = Model(Input, Parameters, Context)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.contracts import Contract
from app.shared.ids import new_id, slugify, utcnow


class ModelType(str, Enum):
    """Catalogue categories used by the UI. The domain treats them alike."""

    MACHINE_LEARNING = "machine_learning"
    STATISTICAL = "statistical"
    MATHEMATICAL = "mathematical"
    RULE = "rule"
    OPTIMIZATION = "optimization"
    SIMULATION = "simulation"
    FORMULA = "formula"
    #  A model whose computation is a language model's reasoning over evidence
    #  the platform assembled. It is a category rather than a special case:
    #  it is versioned, executed, compared and traced like every other, and
    #  `test_model_type_coverage.py` requires it to have a provider.
    LLM = "llm"
    CUSTOM = "custom"


class RuntimeKind(str, Enum):
    """Where a model's computation actually happens.

    A Model is a definition; a Runtime is what executes it. They are separate on
    purpose - the same formula could run in Python today and WASM tomorrow.
    """

    PYTHON = "python"
    SQL = "sql"
    CONTAINER = "container"
    EXTERNAL_API = "external_api"
    RULE_ENGINE = "rule_engine"
    NATIVE = "native"


class ModelStatus(str, Enum):
    """Whether a model is offered for new work.

    Deliberately two states, not a workflow. "Draft" and "published" are not
    here because they are *facts*, computed by comparing the working definition
    to the current version — a status somebody sets by hand can disagree with
    what the model actually is, and then it is decoration.

    ACTIVE      offered everywhere.
    DEPRECATED  still executes, so history stays reproducible, but is kept out
                of pickers and the default listing. This is what you want for a
                model whose results are still referenced but which nobody
                should build anything new on.
    """

    ACTIVE = "active"
    DEPRECATED = "deprecated"



@dataclass
class ModelDefinition:
    """Identity + contracts + runtime. The provider decides how to execute it."""

    name: str
    provider: str                      # plugin key, e.g. "formula" / "sklearn"
    type: ModelType = ModelType.CUSTOM
    runtime: RuntimeKind = RuntimeKind.PYTHON
    status: ModelStatus = ModelStatus.ACTIVE
    description: str = ""
    slug: str = ""
    input_contract: Contract = field(default_factory=Contract)
    parameter_contract: Contract = field(default_factory=Contract)
    output_contract: Contract = field(default_factory=Contract)
    configuration: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    current_version_id: str | None = None
    id: str = field(default_factory=lambda: new_id("mdl"))
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

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slugify(self.name)

    @property
    def is_trainable(self) -> bool:
        """Declared by the provider, mirrored here for listings."""
        return bool(self.metadata.get("trainable", False))


def definition_from_snapshot(
    model: ModelDefinition, snapshot: dict[str, Any]
) -> ModelDefinition:
    """Rebuild the definition a version froze, keeping the model's identity.

    Identity (id, name, slug) belongs to the model and follows it; behaviour
    (contracts, configuration) belongs to the version and must not. Rebuilding
    rather than mutating means the caller's model object is untouched.
    """
    from copy import deepcopy

    from app.shared.contracts import Contract

    return replace(
        model,
        provider=snapshot.get("provider", model.provider),
        type=ModelType(snapshot["type"]) if snapshot.get("type") else model.type,
        runtime=(
            RuntimeKind(snapshot["runtime"]) if snapshot.get("runtime") else model.runtime
        ),
        input_contract=Contract.from_dict(snapshot.get("input_contract")),
        parameter_contract=Contract.from_dict(snapshot.get("parameter_contract")),
        output_contract=Contract.from_dict(snapshot.get("output_contract")),
        configuration=deepcopy(snapshot.get("configuration") or {}),
        metadata=deepcopy(snapshot.get("metadata") or {}),
    )


@dataclass
class ModelVersion:
    """An immutable snapshot of a model plus, optionally, a trained artifact.

    Versions are never edited. Changing a model produces the next version.
    """

    model_id: str
    version: int
    definition_snapshot: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    artifact_uri: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    created_by_execution_id: str | None = None
    notes: str = ""
    id: str = field(default_factory=lambda: new_id("mv"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def label(self) -> str:
        return f"v{self.version}"
