"""The risk matrix as a Model.

Rule-typed, not statistical: the answer comes from a grid somebody signed off,
and the provider's job is to read the right cell and say which one it read.
"""

from __future__ import annotations

from typing import Any

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

from .matrix import matrix_from_config

PLUGIN_KEY = "risk-matrix"


def _axis_spec(name: str, description: str) -> FieldSpec:
    return FieldSpec(
        name,
        FieldType.JSON,
        description=description,
        fields=(
            FieldSpec("column", FieldType.STRING,
                      description="the column this axis reads"),
            FieldSpec("levels", FieldType.ARRAY,
                      description="the axis's levels, least severe first",
                      item=FieldSpec(name="level", type=FieldType.STRING)),
            FieldSpec("bands", FieldType.ARRAY, required=False,
                      description=(
                          "ascending boundaries turning a number into a level; "
                          "one fewer than there are levels. Leave empty when the "
                          "column already holds a level"
                      ),
                      item=FieldSpec(name="boundary", type=FieldType.FLOAT)),
            FieldSpec("default", FieldType.STRING, required=False,
                      description="level used when the column has no reading"),
        ),
    )


class RiskMatrixPlugin:
    """Likelihood against consequence, read from a declared grid."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Risk matrix",
            model_type=ModelType.RULE,
            runtime=RuntimeKind.RULE_ENGINE,
            version="1",
            description=(
                "Reads a likelihood level and a consequence level off two "
                "columns and returns the cell of a declared grid — with the "
                "two levels and the reason for each beside the answer."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.EVALUATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    _axis_spec("likelihood", "how likely the event is"),
                    _axis_spec("consequence", "how bad it would be"),
                    FieldSpec(
                        "grid",
                        FieldType.ARRAY,
                        description=(
                            "one row per likelihood level, one cell per "
                            "consequence level"
                        ),
                        item=FieldSpec(
                            name="row",
                            type=FieldType.ARRAY,
                            item=FieldSpec(name="cell", type=FieldType.STRING),
                        ),
                    ),
                    FieldSpec(
                        "severity_order",
                        FieldType.ARRAY,
                        required=False,
                        description=(
                            "the grid's outcomes, least severe first, so two "
                            "answers can be compared without knowing the words"
                        ),
                        item=FieldSpec(name="level", type=FieldType.STRING),
                    ),
                    FieldSpec("output", FieldType.STRING, required=False,
                              default="risk_level",
                              description="name of the risk column"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per subject, carrying both axis columns",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="the input plus the risk level, both levels and the basis",
            ),
            examples=[
                {
                    "name": "3x4 operational risk",
                    "configuration": {
                        "likelihood": {
                            "column": "failure_indication",
                            "levels": ["low", "medium", "high"],
                            "bands": [0.33, 0.66],
                            "default": "low",
                        },
                        "consequence": {
                            "column": "criticality",
                            "levels": ["low", "medium", "high", "critical"],
                        },
                        "grid": [
                            ["LOW", "LOW", "MEDIUM", "HIGH"],
                            ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                            ["MEDIUM", "HIGH", "CRITICAL", "CRITICAL"],
                        ],
                        "severity_order": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        try:
            matrix_from_config(definition.configuration or {})
        except ValidationError as exc:
            result.add_error(exc.message)
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        result = ValidationResult()
        try:
            matrix = matrix_from_config(definition.configuration or {})
        except ValidationError as exc:
            return result.add_error(exc.message)
        available = {f.name for f in schema_fields}
        for axis in (matrix.likelihood, matrix.consequence):
            if axis.column not in available:
                message = f"no column '{axis.column}' for the {axis.name} axis"
                if axis.default is None:
                    result.add_error(message)
                else:
                    result.add_warning(f"{message}; '{axis.default}' will be used")
        return result

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        matrix = matrix_from_config(config)
        output = str(config.get("output") or "risk_level")

        records = (
            context.input.table.to_rows()
            if context.input.has_table
            else [context.input.record]
        )
        if not records or records == [{}]:
            raise ValidationError(
                "a risk matrix needs either a dataset or an inline input record"
            )

        assessed: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for record in records:
            answer = matrix.assess(record)
            level = answer["risk_level"]
            counts[str(level)] = counts.get(str(level), 0) + 1
            assessed.append(
                {
                    **record,
                    output: level,
                    f"{output}_likelihood": answer["likelihood"],
                    f"{output}_consequence": answer["consequence"],
                    f"{output}_rank": answer["rank"],
                    f"{output}_basis": answer["basis"],
                }
            )

        single = len(assessed) == 1 and not context.input.has_table
        if single:
            payload = ResultPayload(
                kind=ResultKind.CLASSIFICATION,
                value=assessed[0],
                summary={"levels": counts},
            )
        else:
            payload = ResultPayload.of_table(
                Table.from_rows(assessed),
                kind=ResultKind.TABLE,
                summary={"levels": counts},
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            )

        return ExecutionOutcome(
            payload=payload,
            metrics={
                "assessed": len(assessed),
                **{f"level_{k}": v for k, v in counts.items()},
            },
            logs=context.logs,
        )
