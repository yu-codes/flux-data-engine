"""Typhoon analog model provider.

Wraps the preserved similarity algorithms - Coastline, Coastline-RRF, Combined
RRF, weighted KNN and the CWA rule classifier - as a platform Model.

It is a statistical (analog-ensemble) model, not a machine-learning one: there
is nothing to train, and it still travels the same
Data -> Model -> Execution -> Result path as every other provider. That is
precisely the point of keeping Model broader than MLModel.
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

from .algorithms.regions import region_codes
from .engine import (
    DEFAULT_BUFFER_KM,
    DEFAULT_METHOD,
    METHODS,
    coastline_geometry,
    get_engine,
    track_coords,
    track_dataframe,
)

PLUGIN_KEY = "typhoon-analog"

MAX_K = 20


class TyphoonAnalogPlugin:
    """Executable-only provider: an analog ensemble over historical tracks."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Typhoon analog",
            model_type=ModelType.STATISTICAL,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Finds the historical typhoons whose tracks most closely match a "
                "query track, then votes on the CWA landfall-track category. "
                "Coastline-RRF fuses absolute-position ranking with weighted-KNN "
                "and optional event-rainfall ranking."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.PREDICTION, ExecutionKind.SIMULATION),
            input_contract=Contract(
                shape=ContractShape.FREE,
                description=(
                    "Either {'track': [{latitude, longitude, wind_kt?, "
                    "pressure_mb?}...]} with at least 2 points, or "
                    "{'typhoon_id': '<id>'} to replay a historical typhoon."
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
                        description="similarity method",
                    ),
                    FieldSpec(
                        name="k",
                        type=FieldType.INTEGER,
                        required=False,
                        default=5,
                        description="number of analogs to return",
                    ),
                    FieldSpec(
                        name="buffer_km",
                        type=FieldType.FLOAT,
                        required=False,
                        default=DEFAULT_BUFFER_KM,
                        unit="km",
                        description="coastline buffer that bounds the whole computation",
                    ),
                    FieldSpec(
                        name="use_rainfall",
                        type=FieldType.BOOLEAN,
                        required=False,
                        default=False,
                        description="add an event-rainfall ranking to the fusion",
                    ),
                    FieldSpec(
                        name="rainfall_region",
                        type=FieldType.STRING,
                        required=False,
                        default="tn",
                        enum=tuple(region_codes()),
                    ),
                    FieldSpec(
                        name="rainfall_weight",
                        type=FieldType.FLOAT,
                        required=False,
                        description="RRF weight of the rainfall ranking",
                    ),
                    FieldSpec(
                        name="expected_rainfall",
                        type=FieldType.FLOAT,
                        required=False,
                        unit="mm",
                        description="expected event rainfall for the query typhoon",
                    ),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.OBJECT,
                description="predicted category, vote distribution and ranked analogs",
                fields=[
                    FieldSpec("predicted_category", FieldType.STRING, nullable=True),
                    FieldSpec("confidence", FieldType.FLOAT),
                    FieldSpec("category_votes", FieldType.JSON),
                    FieldSpec("analogs", FieldType.ARRAY),
                    FieldSpec("buffer_km", FieldType.FLOAT, unit="km"),
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
                    "name": "Coastline RRF, 5 analogs",
                    "parameters": {
                        "method": "coastline_rrf",
                        "k": 5,
                        "buffer_km": 500,
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
            result.add_error(
                f"configuration.method must be one of {sorted(METHODS)}"
            )
        k = config.get("k", 5)
        if not isinstance(k, int) or not 1 <= k <= MAX_K:
            result.add_error(f"configuration.k must be an integer between 1 and {MAX_K}")
        buffer_km = config.get("buffer_km", DEFAULT_BUFFER_KM)
        if not isinstance(buffer_km, (int, float)) or not 50 <= float(buffer_km) <= 2000:
            result.add_error("configuration.buffer_km must be between 50 and 2000")
        region = config.get("rainfall_region", "tn")
        if region not in region_codes():
            result.add_error(
                f"configuration.rainfall_region must be one of {region_codes()}"
            )
        return result

    # -- execution ---------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        params = {**(context.definition.configuration or {}), **context.parameters}
        method = str(params.get("method", DEFAULT_METHOD))
        k = int(params.get("k", 5))
        if not 1 <= k <= MAX_K:
            raise ValidationError(f"k must be between 1 and {MAX_K}")
        buffer_km = float(params.get("buffer_km", DEFAULT_BUFFER_KM))

        #  The preserved algorithms report progress on stdout. Capture it into
        #  the execution log rather than the server console: it belongs with the
        #  run, and it leaves that code untouched.
        captured = io.StringIO()
        with redirect_stdout(captured):
            engine = get_engine(
                method=method,
                buffer_km=buffer_km,
                **_catalogue(context),
            )
            query = self._resolve_query(context, engine)
            prediction = engine.predict_track(
                query["track_df"],
                k=k,
                use_rainfall=bool(params.get("use_rainfall", False)),
                rainfall_region=str(params.get("rainfall_region", "tn")),
                rainfall_weight=_optional_float(params.get("rainfall_weight")),
                expected_rainfall=_optional_float(params.get("expected_rainfall")),
            )
        for line in captured.getvalue().splitlines():
            if line.strip():
                context.log(line.strip())
        context.log(f"method={method} buffer_km={buffer_km:g} k={k}")

        geometry = coastline_geometry(buffer_km)
        value: dict[str, Any] = {
            "method": prediction.method,
            "method_description": METHODS[prediction.method],
            "predicted_category": prediction.predicted_category,
            "confidence": prediction.confidence,
            "category_votes": prediction.category_votes,
            "analogs": prediction.analogs,
            "rainfall": prediction.rainfall,
            "buffer_km": prediction.buffer_km,
            "distance_unit": prediction.distance_unit,
            "query": {
                "typhoon_id": query["typhoon_id"],
                "track": track_coords(query["track_df"], buffer_km),
            },
            "geometry": geometry,
        }

        top = prediction.analogs[0] if prediction.analogs else None
        return ExecutionOutcome(
            payload=ResultPayload(
                kind=ResultKind.CLASSIFICATION,
                value=value,
                summary={
                    "method": prediction.method,
                    "predicted_category": prediction.predicted_category,
                    "confidence": prediction.confidence,
                    "analog_count": len(prediction.analogs),
                    "closest_analog": top["typhoon_id"] if top else None,
                },
            ),
            metrics={
                "analog_count": len(prediction.analogs),
                "confidence": prediction.confidence,
                "closest_offset_km": top["offset_km"] if top else None,
                "mean_offset_km": (
                    round(
                        sum(a["offset_km"] for a in prediction.analogs)
                        / len(prediction.analogs),
                        1,
                    )
                    if prediction.analogs
                    else None
                ),
            },
            logs=context.logs,
        )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _resolve_query(context: ExecutionContext, engine) -> dict[str, Any]:
        """Accept an explicit track, a historical typhoon id, or a track dataset."""
        record = context.input.record or {}

        if record.get("typhoon_id"):
            typhoon_id = str(record["typhoon_id"])
            try:
                historical = engine.loader.get(typhoon_id)
            except KeyError as exc:
                raise ValidationError(f"unknown typhoon id '{typhoon_id}'") from exc
            return {"typhoon_id": typhoon_id, "track_df": historical.track}

        if record.get("track"):
            return {"typhoon_id": None, "track_df": track_dataframe(record["track"])}

        if context.input.has_table:
            rows = context.input.rows()
            if not rows or "latitude" not in rows[0]:
                raise ValidationError(
                    "the input dataset must have 'latitude' and 'longitude' columns"
                )
            return {"typhoon_id": None, "track_df": track_dataframe(rows)}

        raise ValidationError(
            "provide either input.track (>= 2 points), input.typhoon_id, "
            "or a dataset of track points"
        )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
