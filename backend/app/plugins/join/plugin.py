"""Joining two tables: the operation the graph could not express.

A pipeline branched but never merged, because every provider read exactly one
table. That is a hard limitation for a data platform - joining is the most
common thing anybody does to two tables - and it was a limitation of the
contract, not of the graph.

This is the first provider that reads two, and it exists as much to prove the
plural input works as to do the join.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
)
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
)
from app.shared.errors import ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

PLUGIN_KEY = "join"

#  The name a pipeline wires the second table under. One name, published in the
#  contract, so a step and this provider agree without either guessing.
RIGHT_INPUT = "right"

HOW = ("inner", "left", "full")


class JoinPlugin:
    """Combine the step's input with a second table on shared key columns."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Join tables",
            model_type=ModelType.CUSTOM,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Joins the incoming table with a second one on one or more key "
                "columns. Wire the second table into the step as 'right'."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.TRANSFORMATION, ExecutionKind.CALCULATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        "on",
                        FieldType.ARRAY,
                        description="key columns present in both tables",
                        item=FieldSpec(name="column", type=FieldType.STRING),
                    ),
                    FieldSpec(
                        "how",
                        FieldType.STRING,
                        required=False,
                        default="inner",
                        enum=HOW,
                        description=(
                            "inner keeps matches only; left keeps every row of "
                            "the incoming table; full keeps everything"
                        ),
                    ),
                    FieldSpec(
                        "suffix",
                        FieldType.STRING,
                        required=False,
                        default="_right",
                        description="added to columns that collide",
                    ),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE, description="the left-hand table"
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE, description="the joined table"
            ),
            examples=[
                {
                    "name": "Attach prices to orders",
                    "description": (
                        "Orders in, prices wired in as 'right', joined on the "
                        "product column."
                    ),
                    "configuration": {"on": ["product"], "how": "left"},
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        keys = config.get("on") or []
        if not keys:
            result.add_error("a join needs at least one key column in 'on'")
        elif not all(isinstance(k, str) and k for k in keys):
            result.add_error("every entry of 'on' must be a column name")
        how = config.get("how", "inner")
        if how not in HOW:
            result.add_error(f"unknown join type '{how}'; expected one of {list(HOW)}")
        return result

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        keys = [str(k) for k in (config.get("on") or [])]
        how = str(config.get("how", "inner"))
        suffix = str(config.get("suffix", "_right"))

        left = context.input.table
        right = context.input.named(RIGHT_INPUT)
        if left is None:
            raise ValidationError("a join needs an incoming table")
        if right is None:
            raise ValidationError(
                f"a join needs a second table wired in as '{RIGHT_INPUT}'"
            )

        missing_left = [k for k in keys if k not in left.columns]
        missing_right = [k for k in keys if k not in right.columns]
        if missing_left or missing_right:
            #  Name the columns and the side they are missing from. "The keys
            #  are not present in both tables" tells somebody staring at two
            #  forty-column tables nothing they can act on.
            parts = []
            if missing_left:
                parts.append(f"the incoming table has no {missing_left}")
            if missing_right:
                parts.append(f"the '{RIGHT_INPUT}' table has no {missing_right}")
            raise ValidationError(
                f"cannot join on {keys}: " + "; ".join(parts),
                details={
                    "missing_from_input": missing_left,
                    "missing_from_right": missing_right,
                    "input_columns": left.columns,
                    "right_columns": right.columns,
                },
            )

        #  Arrow's join needs the key columns to agree on type, which two
        #  independently-ingested tables often do not: a code read as an
        #  integer on one side and as text on the other would silently match
        #  nothing. Aligning them on text is the one choice that always works.
        left_arrow, right_arrow = _aligned(left.arrow, right.arrow, keys)

        joined = left_arrow.join(
            right_arrow,
            keys=keys,
            join_type=_ARROW_JOIN[how],
            right_suffix=suffix,
        )
        table = Table(joined)
        context.log(
            f"{left.num_rows} rows joined with {right.num_rows} on "
            f"{keys} ({how}) -> {table.num_rows}"
        )

        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                table,
                kind=ResultKind.TABLE,
                summary={"on": keys, "how": how},
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "rows_left": left.num_rows,
                "rows_right": right.num_rows,
                "rows_out": table.num_rows,
                #  Worth reporting: a join that silently drops most of one side
                #  is usually a mistake about the keys, not about the data.
                "unmatched_left": max(left.num_rows - table.num_rows, 0),
            },
            logs=context.logs,
        )


_ARROW_JOIN = {
    "inner": "inner",
    "left": "left outer",
    "full": "full outer",
}


def _aligned(left: pa.Table, right: pa.Table, keys: list[str]) -> tuple[pa.Table, pa.Table]:
    """Make the key columns comparable, casting to text where they differ."""
    import pyarrow.compute as pc

    for key in keys:
        if left.schema.field(key).type == right.schema.field(key).type:
            continue
        for table_name, table in (("left", left), ("right", right)):
            index = table.column_names.index(key)
            cast = pc.cast(table.column(key), pa.string(), safe=False)
            if table_name == "left":
                left = left.set_column(index, key, cast)
            else:
                right = right.set_column(index, key, cast)
    return left, right


def describe_join_input() -> dict[str, Any]:
    """What a pipeline step must wire in for this provider to work."""
    return {"right": "the table to join against"}
