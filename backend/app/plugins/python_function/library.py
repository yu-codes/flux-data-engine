"""Registry of vetted Python transforms available to Custom models.

Deliberately a registry rather than an eval() of user-supplied source: the API
is reachable over the network, so arbitrary code execution is not offered.
Adding a transform is a code change, reviewed like any other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.shared.contracts import Contract, ContractShape, FieldSpec, FieldType
from app.shared.errors import ValidationError
from app.shared.tabular import Table

from . import columnar

#  Two shapes are allowed, and only one of them should be used.
#
#  `table_fn` takes a Table and returns a Table. Every standard transform is
#  written this way, and `columnar.py` holds them: rebuilding forty thousand
#  dicts in order to drop a column was the single largest cost in a pipeline.
#
#  `fn` takes rows and returns rows. It was the original shape, and it is kept
#  only because it is the seam the migration happened through - `apply()` hides
#  which shape a transform uses, so they could be moved a few at a time rather
#  than in one commit. Nothing uses it now, and a test asserts that no
#  registered transform materialises its input as rows. A new transform that
#  reaches for it is a new transform written the slow way.
TransformFn = Callable[[list[dict], dict[str, Any]], list[dict]]
TableTransformFn = Callable[["Table", dict[str, Any]], "Table"]


@dataclass
class Transform:
    key: str
    name: str
    description: str
    fn: TransformFn | None = None
    parameters: Contract = field(default_factory=Contract)
    table_fn: TableTransformFn | None = None

    def __post_init__(self) -> None:
        if self.fn is None and self.table_fn is None:
            raise ValueError(f"transform '{self.key}' has no implementation")

    @property
    def is_columnar(self) -> bool:
        """Whether this transform runs without materialising rows."""
        return self.table_fn is not None

    def apply(self, table: Table, options: dict[str, Any]) -> Table:
        """Run the transform, in whichever shape it was written."""
        if self.table_fn is not None:
            return self.table_fn(table, options)
        return Table.from_rows(self.fn(table.to_rows(), options))


_REGISTRY: dict[str, Transform] = {}


def register(transform: Transform) -> None:
    _REGISTRY[transform.key] = transform


def get(key: str) -> Transform:
    transform = _REGISTRY.get(key)
    if transform is None:
        raise ValidationError(
            f"unknown transform '{key}'", details={"available": sorted(_REGISTRY)}
        )
    return transform


def keys() -> list[str]:
    return sorted(_REGISTRY)


def catalogue() -> list[dict]:
    return [
        {
            "key": t.key,
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters.to_dict(),
            "columnar": t.is_columnar,
        }
        for t in sorted(_REGISTRY.values(), key=lambda t: t.name)
    ]


# --------------------------------------------------------------------------
# built-in transforms
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# registration
#
# Each transform states its parameters as a Contract, which is what lets the
# UI build a form for a step without knowing what the step does.
# --------------------------------------------------------------------------
def _object(*fields: FieldSpec) -> Contract:
    return Contract(shape=ContractShape.OBJECT, fields=list(fields))


_BUILTINS = [
    Transform(
        key="parse_numeric",
        name="Parse numeric",
        description=(
            "Lifts the leading number out of a text column into a numeric one, "
            "leaving values it cannot read null."
        ),
        table_fn=columnar.parse_numeric,
        parameters=_object(
            FieldSpec("column", FieldType.STRING, description="the text column to read"),
            FieldSpec(
                "output",
                FieldType.STRING,
                required=False,
                description="new column name; defaults to <column>_value",
            ),
            FieldSpec(
                "keep_original",
                FieldType.BOOLEAN,
                required=False,
                default=True,
                description="keep the text column alongside the number",
            ),
        ),
    ),
    Transform(
        key="filter_rows",
        name="Filter rows",
        description="Keeps only the rows matching one condition on one column.",
        table_fn=columnar.filter_rows,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec(
                "op",
                FieldType.STRING,
                required=False,
                default="not_empty",
                enum=(
                    "not_empty", "is_empty", "equals", "not_equals",
                    "gt", "gte", "lt", "lte", "in",
                ),
            ),
            FieldSpec(
                "value",
                FieldType.ANY,
                required=False,
                description="what to compare against; a list for 'in'",
            ),
        ),
    ),
    Transform(
        key="select_columns",
        name="Select columns",
        description="Keeps only the named columns, in the order given.",
        table_fn=columnar.select_columns,
        parameters=_object(
            FieldSpec("columns", FieldType.ARRAY, description="columns to keep"),
        ),
    ),
    Transform(
        key="moving_average",
        name="Moving average",
        description="Adds a trailing-window mean of a numeric column.",
        table_fn=columnar.moving_average,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("window", FieldType.INTEGER, required=False, default=3),
            FieldSpec(
                "output",
                FieldType.STRING,
                required=False,
                description="new column name; defaults to <column>_ma<window>",
            ),
        ),
    ),
    Transform(
        key="zscore_outliers",
        name="Z-score outliers",
        description=(
            "Adds a z-score and an outlier flag for a numeric column, using the "
            "column's own mean and standard deviation."
        ),
        table_fn=columnar.zscore_outliers,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec(
                "threshold",
                FieldType.FLOAT,
                required=False,
                default=3.0,
                description="standard deviations beyond which a row is an outlier",
            ),
        ),
    ),
    Transform(
        key="group_aggregate",
        name="Group and aggregate",
        description="Collapses rows to one per group, with an aggregate and a count.",
        table_fn=columnar.group_aggregate,
        parameters=_object(
            FieldSpec("group_by", FieldType.STRING, description="the grouping column"),
            FieldSpec("column", FieldType.STRING, description="the column to aggregate"),
            FieldSpec(
                "agg",
                FieldType.STRING,
                required=False,
                default="sum",
                enum=("sum", "mean", "min", "max", "count"),
            ),
        ),
    ),
]

for _transform in _BUILTINS:
    register(_transform)
