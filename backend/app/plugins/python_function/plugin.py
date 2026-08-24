"""Custom Python transform provider.

Model type CUSTOM with a Python runtime. The model definition names a
registered transform; the platform never executes user-supplied source.
"""

from __future__ import annotations

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

from . import library

PLUGIN_KEY = "python-transform"


class PythonTransformPlugin:
    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Python transform",
            model_type=ModelType.CUSTOM,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Runs a vetted Python transform over the input table. "
                "Transforms are registered in code, not supplied at runtime."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.TRANSFORMATION, ExecutionKind.CALCULATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        name="transform",
                        type=FieldType.STRING,
                        description="registered transform key",
                        enum=tuple(library.keys()),
                    ),
                    FieldSpec(
                        name="options",
                        type=FieldType.JSON,
                        required=False,
                        description="transform-specific options",
                    ),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE, description="transformed rows"
            ),
            examples=[
                {
                    "name": "7-day moving average",
                    "configuration": {
                        "transform": "moving_average",
                        "options": {"column": "revenue", "window": 7},
                    },
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        key = config.get("transform")
        if not key:
            return result.add_error("configuration.transform is required")
        try:
            transform = library.get(str(key))
        except ValidationError as exc:
            return result.add_error(exc.message)
        options_check = transform.parameters.validate_record(config.get("options") or {})
        return result.merge(options_check)

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        transform = library.get(str(config.get("transform")))
        options = {**(config.get("options") or {})}
        #  Request parameters may override individual transform options.
        options.update(
            {k: v for k, v in context.parameters.items()
             if k in transform.parameters.names}
        )

        validation = transform.parameters.validate_record(options)
        if not validation.valid:
            raise ValidationError(
                f"invalid options for transform '{transform.key}'",
                details=validation.to_dict(),
            )

        if not context.input.has_table:
            raise ValidationError("a Python transform needs a dataset as input")

        #  The transform decides whether it wants rows; a columnar one never
        #  builds them, which is the difference between a twelve-step pipeline
        #  costing twelve full materialisations and costing none.
        source = context.input.table
        table = transform.apply(source, transform.parameters.coerce_record(options))

        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                table,
                kind=ResultKind.TABLE,
                summary={"transform": transform.key, "options": options},
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "rows_in": source.num_rows,
                "rows_out": table.num_rows,
                "columnar": transform.is_columnar,
            },
            logs=context.logs,
        )
