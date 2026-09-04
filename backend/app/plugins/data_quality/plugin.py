"""Data quality as a Model.

It is a Model rather than a transform because its answer is a *judgement about
a dataset*, not a reshaping of one: it takes readings in and produces one row
per series with a score, the counts behind the score, and the sentences a
person needs to decide whether to trust what follows.

Running it as an ordinary step means the verdict is versioned, scheduled,
compared and traced like anything else — which is the difference between a
data-quality layer and a script somebody runs when a chart looks wrong.
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

from .checks import CHECKS, assess_series

PLUGIN_KEY = "data-quality"


class DataQualityPlugin:
    """Score every measurement series before anything draws conclusions from it."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Data quality",
            model_type=ModelType.STATISTICAL,
            runtime=RuntimeKind.PYTHON,
            version="1",
            description=(
                "One row per measurement series: missing readings, sampling "
                "gaps, outliers, stuck instruments, impossible values and level "
                "drift — with a 0-100 score and the sentences behind it."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.EVALUATION, ExecutionKind.CALCULATION),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec("value", FieldType.STRING,
                              description="the column holding the reading"),
                    FieldSpec("checks", FieldType.ARRAY, required=False,
                              description=(
                                  "which checks apply; leave empty for all. A "
                                  "series mixing two operating regimes has its "
                                  "idle readings as outliers and its nights as "
                                  "gaps, so the answer depends on what the "
                                  "series is"
                              ),
                              item=FieldSpec(name="check", type=FieldType.STRING,
                                             enum=CHECKS)),
                    FieldSpec("timestamp", FieldType.STRING, required=False,
                              description="when the reading was taken"),
                    FieldSpec("group_by", FieldType.ARRAY, required=False,
                              description="columns identifying one series",
                              item=FieldSpec(name="column", type=FieldType.STRING)),
                    FieldSpec("expected_interval_minutes", FieldType.FLOAT,
                              required=False,
                              description="declared sampling interval; gaps are "
                                          "measured against it"),
                    FieldSpec("gap_tolerance", FieldType.FLOAT, required=False,
                              default=2.0,
                              description="multiples of the interval before a gap counts"),
                    FieldSpec("flatline_readings", FieldType.INTEGER, required=False,
                              default=6,
                              description="identical readings in a row before the "
                                          "instrument is called stuck"),
                    FieldSpec("step_ratio", FieldType.FLOAT, required=False,
                              default=12.0,
                              description="multiples of the series' own noise a "
                                          "sustained level shift must reach "
                                          "before it is called an instrument "
                                          "event"),
                    FieldSpec("outlier_factor", FieldType.FLOAT, required=False,
                              default=3.0,
                              description="interquartile ranges beyond the box"),
                    FieldSpec(
                        "ranges",
                        FieldType.JSON,
                        required=False,
                        description=(
                            "physical limits per series key, e.g. "
                            "{\"vibration_rms\": {\"min\": 0, \"max\": 60}}"
                        ),
                    ),
                    FieldSpec("range_key", FieldType.STRING, required=False,
                              description="which grouping column the ranges are keyed by"),
                    FieldSpec("drift_sigma", FieldType.FLOAT, required=False,
                              default=3.0,
                              description="level shift between the halves worth reporting"),
                    FieldSpec("min_score", FieldType.FLOAT, required=False,
                              default=0.0,
                              description="series scoring below this are reported "
                                          "but marked unusable"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description="long readings: one row per series per observation",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per series, with counts, a score and the issues",
            ),
            examples=[
                {
                    "name": "Hourly sensor health",
                    "configuration": {
                        "value": "value",
                        "timestamp": "timestamp",
                        "group_by": ["asset_id", "parameter"],
                        "expected_interval_minutes": 60,
                        "flatline_readings": 6,
                    },
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        if not config.get("value"):
            result.add_error("configuration.value must name the reading column")
        ranges = config.get("ranges")
        if ranges is not None and not isinstance(ranges, dict):
            result.add_error("configuration.ranges must be an object keyed by series")
        if ranges and not config.get("range_key"):
            result.add_warning(
                "ranges are declared but range_key is not, so no series will match one"
            )
        unknown = sorted(set(as_list(config.get("checks"))) - set(CHECKS))
        if unknown:
            result.add_error(f"unknown checks: {unknown}")
        tolerance = config.get("gap_tolerance", 2.0)
        if tolerance is not None and float(tolerance) < 1:
            result.add_error("gap_tolerance below 1 would call every interval a gap")
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        available = {f.name for f in schema_fields}
        for key in ("value", "timestamp"):
            column = config.get(key)
            if column and column not in available:
                result.add_error(f"dataset has no column '{column}' for '{key}'")
        for column in as_list(config.get("group_by")):
            if column not in available:
                result.add_error(f"dataset has no grouping column '{column}'")
        return result

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        value_column = str(config.get("value") or "")
        time_column = config.get("timestamp")
        group_by = as_list(config.get("group_by"))
        ranges = config.get("ranges") or {}
        range_key = config.get("range_key")
        min_score = float(config.get("min_score", 0.0) or 0.0)
        wanted = tuple(as_list(config.get("checks"))) or CHECKS

        if not context.input.has_table:
            raise ValidationError("a data-quality check needs a dataset as input")
        table = context.input.table
        for column in [c for c in (value_column, time_column, *group_by) if c]:
            if column not in table.columns:
                raise ValidationError(
                    f"column '{column}' is not in the input",
                    details={"available": sorted(table.columns)},
                )

        values = [as_number(v) for v in table.column_values(value_column)]
        stamps = (
            [as_datetime(v) for v in table.column_values(str(time_column))]
            if time_column
            else [None] * table.num_rows
        )
        identity_columns = {name: table.column_values(name) for name in group_by}

        grouped: dict[tuple, list[int]] = {}
        identities: dict[tuple, dict[str, Any]] = {}
        for index in range(table.num_rows):
            key = tuple(str(identity_columns[n][index]) for n in group_by)
            grouped.setdefault(key, []).append(index)
            identities.setdefault(key, {n: identity_columns[n][index] for n in group_by})

        rows: list[dict[str, Any]] = []
        flags: dict[str, int] = {}
        for key, positions in grouped.items():
            #  Sorted by time where there is one, so "consecutive" and "gap"
            #  mean what they say however the store returned the rows.
            ordered = sorted(
                positions,
                key=lambda i: (stamps[i] is None, stamps[i] or 0)
                if time_column
                else i,
            )
            seen: set = set()
            duplicated = 0
            for index in ordered:
                moment = stamps[index]
                if moment is not None:
                    if moment in seen:
                        duplicated += 1
                    seen.add(moment)

            limits = {}
            if range_key and ranges:
                label = str(identities[key].get(range_key, ""))
                limits = ranges.get(label) or {}
                if not isinstance(limits, dict):
                    limits = {}

            quality = assess_series(
                [values[i] for i in ordered],
                [stamps[i] for i in ordered],
                expected_interval_minutes=(
                    float(config["expected_interval_minutes"])
                    if config.get("expected_interval_minutes")
                    else None
                ),
                gap_tolerance=float(config.get("gap_tolerance", 2.0) or 2.0),
                flatline_readings=int(config.get("flatline_readings", 6) or 6),
                step_ratio=float(config.get("step_ratio", 12.0) or 12.0),
                outlier_factor=float(config.get("outlier_factor", 3.0) or 3.0),
                minimum=(
                    float(limits["min"]) if limits.get("min") is not None else None
                ),
                maximum=(
                    float(limits["max"]) if limits.get("max") is not None else None
                ),
                duplicates=duplicated,
                drift_sigma_limit=float(config.get("drift_sigma", 3.0) or 3.0),
                checks=wanted,
            )
            row = quality.to_dict(**identities[key], measurement=value_column)
            row["usable"] = quality.score >= min_score
            flags[quality.flag] = flags.get(quality.flag, 0) + 1
            rows.append(row)

        if not rows:
            raise ValidationError("the input holds no series to assess")

        scores = [row["quality_score"] for row in rows]
        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                Table.from_rows(rows),
                kind=ResultKind.TABLE,
                summary={"series": len(rows), "flags": flags, "checks": list(wanted)},
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "series": len(rows),
                "mean_quality_score": round(sum(scores) / len(scores), 3),
                "worst_quality_score": min(scores),
                "unusable": sum(1 for row in rows if not row["usable"]),
                **{f"flag_{name}": count for name, count in flags.items()},
            },
            logs=context.logs,
        )
