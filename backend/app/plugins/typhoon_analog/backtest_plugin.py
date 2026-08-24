"""Typhoon backtest provider - model validation as a first-class Execution.

Leave-one-out over the historical record: each typhoon is scored with itself
excluded from the candidate pool, and the analog vote is compared with the CWA
category forecasters actually assigned.

This is what makes `Evaluation` more than a label in this platform. It is also
why the Model abstraction has to be broader than "trainable": nothing is fitted
here, yet the model is unambiguously being validated.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from typing import Any

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
    RequiredDataset,
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

from .engine import (
    DEFAULT_BUFFER_KM,
    DEFAULT_METHOD,
    METHODS,
    VALID_CATEGORIES,
    get_engine,
)

PLUGIN_KEY = "typhoon-backtest"

DEFAULT_SAMPLE = 25
MAX_SAMPLE = 250


class TyphoonBacktestPlugin:
    """Evaluation-only provider: scores an analog method against history."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            #  Coastline methods score roughly a typhoon a second, and the
            #  sample can be 250. The platform default of fifteen minutes is
            #  the wrong number for this one in both directions.
            timeout_seconds=1800,
            name="Typhoon backtest",
            model_type=ModelType.STATISTICAL,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Leave-one-out validation of an analog method against the CWA "
                "landfall-track categories. Produces per-typhoon predictions as "
                "a dataset, plus accuracy, per-category accuracy and a confusion "
                "matrix."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.EVALUATION,),
            input_contract=Contract(
                shape=ContractShape.FREE,
                description=(
                    "No input needed: the historical record is the evaluation set. "
                    "Pass {'typhoon_ids': [...]} to score a specific subset."
                ),
            ),
            parameter_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec(
                        name="method",
                        type=FieldType.STRING,
                        required=False,
                        default=DEFAULT_METHOD,
                        enum=tuple(sorted(METHODS)),
                        description="the similarity method being validated",
                    ),
                    FieldSpec(
                        name="k",
                        type=FieldType.INTEGER,
                        required=False,
                        default=5,
                        description="analogs each vote draws on",
                    ),
                    FieldSpec(
                        name="buffer_km",
                        type=FieldType.FLOAT,
                        required=False,
                        default=DEFAULT_BUFFER_KM,
                        unit="km",
                    ),
                    FieldSpec(
                        name="sample_size",
                        type=FieldType.INTEGER,
                        required=False,
                        default=DEFAULT_SAMPLE,
                        description=(
                            "how many historical typhoons to score; the sample is "
                            "deterministic for a given seed"
                        ),
                    ),
                    FieldSpec(
                        name="seed",
                        type=FieldType.INTEGER,
                        required=False,
                        default=42,
                        description="makes repeat runs comparable",
                    ),
                    FieldSpec(
                        name="categories",
                        type=FieldType.ARRAY,
                        required=False,
                        description="restrict scoring to these CWA categories",
                    ),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per scored typhoon",
                fields=[
                    FieldSpec("typhoon_id", FieldType.STRING),
                    FieldSpec("year", FieldType.INTEGER),
                    FieldSpec("name_en", FieldType.STRING),
                    FieldSpec("true_category", FieldType.STRING),
                    FieldSpec("predicted_category", FieldType.STRING, nullable=True),
                    FieldSpec("confidence", FieldType.FLOAT),
                    FieldSpec("is_correct", FieldType.BOOLEAN, nullable=True),
                ],
            ),
            required_datasets=(
                RequiredDataset(
                    key="catalogue",
                    name="Typhoon catalogue",
                    description=(
                        "Historical typhoons with their tracks and CWA "
                        "landfall-track categories. Swapping this dataset is "
                        "how you re-run against a different record."
                    ),
                    #  Optional, because the bundled file is what the seeder
                    #  ingests to create this dataset in the first place: a
                    #  deployment that has the file but has not ingested it
                    #  still works, and the logs say which was used.
                    required=False,
                ),
            ),
            examples=[
                {
                    "name": "Validate Coastline-RRF on 25 typhoons",
                    "configuration": {
                        "method": "coastline_rrf",
                        "k": 5,
                        "sample_size": 25,
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}

        method = config.get("method", DEFAULT_METHOD)
        if method not in METHODS:
            result.add_error(f"configuration.method must be one of {sorted(METHODS)}")

        sample = config.get("sample_size", DEFAULT_SAMPLE)
        if not isinstance(sample, int) or not 1 <= sample <= MAX_SAMPLE:
            result.add_error(
                f"configuration.sample_size must be an integer between 1 and {MAX_SAMPLE}"
            )
        elif method in ("coastline", "coastline_rrf", "combined_rainfall") and sample > 60:
            #  These compare full track geometry, so cost grows with the sample.
            result.add_warning(
                f"'{method}' scores roughly one typhoon per second; a sample of "
                f"{sample} will take a while"
            )

        categories = config.get("categories")
        if categories is not None:
            unknown = set(categories) - set(VALID_CATEGORIES)
            if not isinstance(categories, list) or unknown:
                result.add_error(
                    f"configuration.categories must be a subset of {VALID_CATEGORIES}"
                )
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        params = {**(context.definition.configuration or {}), **context.parameters}
        method = str(params.get("method", DEFAULT_METHOD))
        k = int(params.get("k", 5))
        buffer_km = float(params.get("buffer_km", DEFAULT_BUFFER_KM))
        sample_size = int(params.get("sample_size", DEFAULT_SAMPLE))
        if not 1 <= sample_size <= MAX_SAMPLE:
            raise ValidationError(f"sample_size must be between 1 and {MAX_SAMPLE}")

        captured = io.StringIO()
        with redirect_stdout(captured):
            engine = get_engine(
                method=method,
                buffer_km=buffer_km,
                **_catalogue(context),
            )
            report = engine.backtest(
                k=k,
                limit=sample_size,
                categories=params.get("categories"),
                seed=int(params.get("seed", 42)),
            )
        for line in captured.getvalue().splitlines():
            if line.strip():
                context.log(line.strip())
        context.log(
            f"scored {report['sample_size']} typhoons with {method} "
            f"(k={k}, buffer={buffer_km:g}km)"
        )

        table = Table.from_rows(
            [
                {
                    "typhoon_id": row["typhoon_id"],
                    "year": row["year"],
                    "name_en": row["name_en"],
                    "name_zh": row.get("name_zh", ""),
                    "true_category": row["true_category"],
                    "predicted_category": row["predicted_category"],
                    "confidence": row["confidence"],
                    "is_correct": row["is_correct"],
                    "top_analog": row.get("top_analog"),
                }
                for row in report["predictions"]
            ]
        )

        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                table,
                kind=ResultKind.TABLE,
                summary={
                    "method": method,
                    "method_description": METHODS[method],
                    "k": k,
                    "buffer_km": buffer_km,
                    "accuracy": report["accuracy"],
                    "correct": report["correct"],
                    "total": report["total"],
                    "per_category": report["per_category"],
                    "confusion": report["confusion"],
                },
                metrics=_metrics(report),
                materialise_as_dataset=True,
                dataset_name=f"Backtest · {method} · k={k}",
            ),
            metrics=_metrics(report),
            logs=context.logs,
        )


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    """Flat, comparable numbers - what an Experiment ranks methods on."""
    per_category = report["per_category"]
    scored = [stats["accuracy"] for stats in per_category.values() if stats["total"]]
    return {
        "accuracy": report["accuracy"],
        "correct": report["correct"],
        "total": report["total"],
        "sample_size": report["sample_size"],
        "categories_scored": len(per_category),
        #  Unweighted mean over classes: a method that only gets the common
        #  categories right scores well on accuracy but poorly here.
        "macro_accuracy": round(sum(scored) / len(scored), 4) if scored else 0.0,
    }


def _catalogue(context: ExecutionContext) -> dict:
    """The historical record this run should read.

    Handed over by the platform because the provider declared it. Falling back
    to the bundled file keeps a deployment that has not ingested the dataset
    working, and says which one it used either way.
    """
    table = context.datasets.get("catalogue")
    if table is None:
        context.log(
            "no catalogue dataset in this workspace; reading the bundled record"
        )
        return {}
    context.log(f"reading {table.num_rows} typhoons from the catalogue dataset")
    return {
        "records": table.to_rows(),
        #  Rows and columns are enough to notice a different dataset without
        #  hashing every value of it on every run.
        "fingerprint": f"{table.num_rows}x{table.num_columns}",
    }
