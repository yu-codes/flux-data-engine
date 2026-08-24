"""Formula model provider.

The simplest possible model: named expressions over the input's columns.

    revenue = price * quantity

No training, no artifact, no ML dependency - and it still travels the same
Data -> Model -> Execution -> Result path as everything else.
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

from .expression import (
    allowed_functions,
    compile_expression,
    evaluate,
    expression_variables,
)

PLUGIN_KEY = "formula"


class FormulaModelPlugin:
    """Executable-only provider: evaluates expressions row by row."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Formula",
            model_type=ModelType.FORMULA,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Named arithmetic expressions evaluated over each input row. "
                "Executable without any training step."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.TRANSFORMATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                description="expressions: {output_name: expression}",
                fields=[
                    FieldSpec(
                        name="expressions",
                        type=FieldType.JSON,
                        description="mapping of output column name to expression",
                    ),
                    FieldSpec(
                        name="keep_input_columns",
                        type=FieldType.BOOLEAN,
                        required=False,
                        default=True,
                        description="carry the input columns into the result",
                    ),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="input rows plus one column per expression",
            ),
            examples=[
                {
                    "name": "Revenue",
                    "configuration": {
                        "expressions": {"revenue": "price * quantity"},
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        expressions = (definition.configuration or {}).get("expressions")
        if not isinstance(expressions, dict) or not expressions:
            return result.add_error(
                "configuration.expressions must be a non-empty mapping of "
                "{output_name: expression}"
            )
        declared = set(definition.input_contract.names)
        for name, source in expressions.items():
            if not str(name).isidentifier():
                result.add_error(f"'{name}' is not a valid output column name")
                continue
            try:
                variables = expression_variables(str(source))
            except ValidationError as exc:
                result.add_error(f"{name}: {exc.message}")
                continue
            #  Expressions may read other expression outputs, evaluated in order.
            unknown = variables - declared - set(expressions)
            if declared and unknown:
                result.add_warning(
                    f"{name}: reads {sorted(unknown)}, which the input contract "
                    f"does not declare"
                )
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        expressions: dict[str, str] = config.get("expressions") or {}
        if not expressions:
            raise ValidationError("this formula model defines no expressions")
        keep_inputs = bool(config.get("keep_input_columns", True))

        compiled = {name: compile_expression(str(src)) for name, src in expressions.items()}

        rows = context.input.rows() if context.input.has_table else [context.input.record]
        if not rows or rows == [{}]:
            raise ValidationError(
                "formula execution needs either a dataset or an inline input record"
            )

        outputs: list[dict[str, Any]] = []
        failures = 0
        for row in rows:
            env: dict[str, Any] = {
                key: value for key, value in row.items() if str(key).isidentifier()
            }
            env.update(
                {k: v for k, v in context.parameters.items() if str(k).isidentifier()}
            )
            computed: dict[str, Any] = {}
            for name, tree in compiled.items():
                try:
                    value = evaluate(tree, env)
                except Exception as exc:  # one bad row must not kill the run
                    value = None
                    failures += 1
                    context.log(f"row skipped for '{name}': {exc}")
                computed[name] = value
                env[name] = value
            outputs.append({**row, **computed} if keep_inputs else computed)

        table = Table.from_rows(outputs)
        single_row = len(outputs) == 1 and not context.input.has_table
        if single_row:
            payload = ResultPayload(
                kind=ResultKind.OBJECT,
                value=outputs[0],
                summary={"expressions": list(expressions)},
            )
        else:
            payload = ResultPayload.of_table(
                table,
                kind=ResultKind.TABLE,
                summary={
                    "expressions": list(expressions),
                    "row_count": table.num_rows,
                },
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            )

        return ExecutionOutcome(
            payload=payload,
            metrics={
                "rows_processed": len(outputs),
                "expression_failures": failures,
                "allowed_functions": len(allowed_functions()),
            },
            logs=context.logs,
        )
