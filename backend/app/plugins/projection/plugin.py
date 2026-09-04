"""Threshold projection as a Model.

Mathematical, not learned: a least-squares line, extended to a declared limit,
reported with the basis it rests on. It reads a long table — one row per
subject per reading — and answers one row per subject.
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
from app.plugins.python_function.columnar import as_datetime, as_list, as_number
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

from .projecting import DIRECTIONS, fit_series, projection_from_config

PLUGIN_KEY = "threshold-projection"

_DAY_SECONDS = 86400.0


class ThresholdProjectionPlugin:
    """When will this reach that, and how much is that answer worth."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Threshold projection",
            model_type=ModelType.MATHEMATICAL,
            runtime=RuntimeKind.PYTHON,
            version="1",
            description=(
                "Fits the recent trend of a measurement per subject and reports "
                "how far it is from each declared limit — as a window in days "
                "with a stated basis (calculated / estimated / inferred / "
                "unknown), never as a bare date."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.PREDICTION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec("value", FieldType.STRING,
                              description="the measurement being projected"),
                    FieldSpec("time", FieldType.STRING,
                              description="the timestamp column it is measured against"),
                    FieldSpec("group_by", FieldType.ARRAY, required=False,
                              description="columns identifying one subject",
                              item=FieldSpec(name="column", type=FieldType.STRING)),
                    FieldSpec(
                        "limits",
                        FieldType.ARRAY,
                        description="the boundaries worth knowing the distance to",
                        item=FieldSpec(
                            name="limit",
                            type=FieldType.JSON,
                            fields=(
                                FieldSpec("name", FieldType.STRING, required=False),
                                FieldSpec("value", FieldType.FLOAT),
                            ),
                        ),
                    ),
                    FieldSpec("direction", FieldType.STRING, required=False,
                              default="rising", enum=DIRECTIONS,
                              description="which way the measurement approaches a limit"),
                    FieldSpec("window", FieldType.INTEGER, required=False, default=30,
                              description="how many recent readings the fit uses"),
                    FieldSpec("min_points", FieldType.INTEGER, required=False, default=5,
                              description="fewer than this can never be 'estimated'"),
                    FieldSpec("min_r_squared", FieldType.FLOAT, required=False,
                              default=0.35,
                              description="a weaker fit is reported as 'inferred'"),
                    FieldSpec("horizon", FieldType.FLOAT, required=False, default=365,
                              description="days beyond which no projection is offered"),
                    FieldSpec("confidence_multiplier", FieldType.FLOAT, required=False,
                              default=1.96,
                              description="standard errors spanned by the reported window"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description="long readings: one row per subject per observation",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per subject per limit, with basis and window",
            ),
            examples=[
                {
                    "name": "Days until a volume is full",
                    "configuration": {
                        "value": "used_pct",
                        "time": "measured_at",
                        "group_by": ["volume"],
                        "limits": [{"name": "warning", "value": 80},
                                   {"name": "full", "value": 100}],
                        "direction": "rising",
                        "window": 30,
                    },
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        if not config.get("value"):
            result.add_error("configuration.value must name the measurement column")
        if not config.get("time"):
            result.add_error("configuration.time must name the timestamp column")
        try:
            projection_from_config(config)
        except ValidationError as exc:
            result.add_error(exc.message)
        window = int(config.get("window", 30) or 30)
        if window < 2:
            result.add_error("window must be at least 2 readings")
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        available = {f.name for f in schema_fields}
        for key in ("value", "time"):
            column = config.get(key)
            if column and column not in available:
                result.add_error(f"dataset has no column '{column}' for '{key}'")
        for column in as_list(config.get("group_by")):
            if column not in available:
                result.add_error(f"dataset has no grouping column '{column}'")
        return result

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        projection = projection_from_config(config)
        value_column = str(config.get("value") or "")
        time_column = str(config.get("time") or "")
        group_by = as_list(config.get("group_by"))
        window = max(2, int(config.get("window", 30) or 30))

        if not context.input.has_table:
            raise ValidationError("a threshold projection needs a dataset as input")
        table = context.input.table
        for column in (value_column, time_column, *group_by):
            if column not in table.columns:
                raise ValidationError(
                    f"column '{column}' is not in the input",
                    details={"available": sorted(table.columns)},
                )

        values = [as_number(v) for v in table.column_values(value_column)]
        stamps = [as_datetime(v) for v in table.column_values(time_column)]
        keys = (
            [
                tuple(str(col[i]) for col in
                      [table.column_values(name) for name in group_by])
                for i in range(table.num_rows)
            ]
            if group_by
            else [()] * table.num_rows
        )
        identity_columns = {name: table.column_values(name) for name in group_by}

        grouped: dict[tuple, list[int]] = {}
        identities: dict[tuple, dict[str, Any]] = {}
        for index, key in enumerate(keys):
            grouped.setdefault(key, []).append(index)
            identities.setdefault(key, {n: identity_columns[n][index] for n in group_by})

        rows: list[dict[str, Any]] = []
        by_basis: dict[str, int] = {}
        for key, positions in grouped.items():
            usable = [
                i for i in positions if values[i] is not None and stamps[i] is not None
            ]
            usable.sort(key=lambda i: stamps[i])
            recent = usable[-window:]
            xs = [stamps[i].timestamp() / _DAY_SECONDS for i in recent]
            ys = [values[i] for i in recent]
            #  The fit is in days from the first reading of the window, so the
            #  intercept means something and the arithmetic stays well
            #  conditioned - epoch days squared is not a number worth summing.
            origin = xs[0] if xs else 0.0
            fit = fit_series([x - origin for x in xs], ys, [stamps[i] for i in recent])
            answer = projection.project(fit)

            for limit in answer["limits"]:
                by_basis[limit["basis"]] = by_basis.get(limit["basis"], 0) + 1
                rows.append(
                    {
                        **identities[key],
                        "measurement": value_column,
                        "limit": limit["limit"],
                        "limit_value": limit["limit_value"],
                        "current_value": fit.last_value,
                        "observed_at": (
                            fit.last_time.isoformat(sep=" ") if fit.last_time else None
                        ),
                        "slope_per_day": answer["slope"],
                        "r_squared": answer["r_squared"],
                        "readings_used": fit.points,
                        "days_to_limit": limit["periods"],
                        "days_earliest": limit.get("periods_earliest"),
                        "days_latest": limit.get("periods_latest"),
                        "projected_date": limit.get("date"),
                        "projected_earliest": limit.get("date_earliest"),
                        "projected_latest": limit.get("date_latest"),
                        "basis": limit["basis"],
                        "reason": limit["reason"],
                    }
                )

        if not rows:
            raise ValidationError(
                "no subject had a readable measurement and timestamp to project"
            )

        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                Table.from_rows(rows),
                kind=ResultKind.TABLE,
                summary={
                    "measurement": value_column,
                    "limits": [limit.name for limit in projection.limits],
                    "basis_counts": by_basis,
                },
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "subjects": len(grouped),
                "projections": len(rows),
                **{f"basis_{name}": count for name, count in by_basis.items()},
            },
            logs=context.logs,
        )
