"""What an execution produces, before anything stores it.

A plugin returns a ResultPayload. It says what shape the answer is and
carries the substance - a table, a scalar, an artifact on disk. It knows
nothing about rows, object stores or ids, which is what lets the plugin
contract stay a pure function and the persistence layer stay replaceable.

This lives in `shared` rather than in the results module because both the
model domain (whose plugins build payloads) and the results module (which
persists them) need it, and neither should depend on the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .tabular import Table


class ResultKind(str, Enum):
    """The shape of an execution's output."""

    SCALAR = "scalar"
    TABLE = "table"
    TIME_SERIES = "time_series"
    CLASSIFICATION = "classification"
    PROBABILITY = "probability"
    OBJECT = "object"
    ARRAY = "array"
    DATASET = "dataset"
    ARTIFACT = "artifact"
    REPORT = "report"


# Payload sizes above this are written to the object store rather than inlined
# in Postgres, keeping metadata rows small.
INLINE_PAYLOAD_MAX_BYTES = 256_000


@dataclass
class ResultPayload:
    """What a plugin hands back. Not yet persisted."""

    kind: ResultKind
    #  Exactly one of ``table`` / ``value`` carries the substance.
    table: Table | None = None
    value: Any = None
    summary: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    #  Local file to be promoted into the object store (model artifacts, plots).
    artifact_path: str | None = None
    materialise_as_dataset: bool = False
    dataset_name: str | None = None

    @classmethod
    def scalar(cls, value: Any, **kwargs) -> ResultPayload:
        return cls(kind=ResultKind.SCALAR, value=value, **kwargs)

    @classmethod
    def of_table(
        cls, table: Table, *, kind: ResultKind = ResultKind.TABLE, **kwargs
    ) -> ResultPayload:
        return cls(kind=kind, table=table, **kwargs)

    @classmethod
    def object(cls, value: dict, **kwargs) -> ResultPayload:
        return cls(kind=ResultKind.OBJECT, value=value, **kwargs)
