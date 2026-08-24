"""Precipitation probability provider (track-relative analog ensemble).

Conditions an ERA5-derived precipitation climatology on the storm's position
and estimates, per grid cell, P(rain >= threshold) and E[rain]. The output is a
distribution over a grid and a sequence of frames - a good example of why a
Result is not always a single "prediction".

The analog database (precip_analog.npz) is built offline; when it is absent the
provider reports itself unavailable rather than failing at execution time.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
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
from app.shared.errors import NotFoundError, ValidationError
from app.shared.payloads import ResultKind, ResultPayload

from .algorithms.precip_analog import (
    DEFAULT_BANDWIDTH_KM,
    DEFAULT_THRESHOLDS,
    PrecipAnalogModel,
    interpolate_track,
)
from .engine import track_dataframe
from .paths import typhoon_data_dir

PLUGIN_KEY = "typhoon-precip-analog"
ANALOG_DB_FILENAME = "precip_analog.npz"
MAX_FRAMES = 60

_MODEL: PrecipAnalogModel | None = None


def analog_db_path() -> Path:
    return typhoon_data_dir() / ANALOG_DB_FILENAME


def is_available() -> bool:
    return analog_db_path().exists()


def get_model() -> PrecipAnalogModel:
    """Load the analog database once per process."""
    global _MODEL
    if _MODEL is None:
        path = analog_db_path()
        if not path.exists():
            raise NotFoundError(
                f"precipitation analog database not found at {path}; "
                "build it before running this model"
            )
        _MODEL = PrecipAnalogModel(path).load()
    return _MODEL


class TyphoonPrecipAnalogPlugin:
    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=PLUGIN_KEY,
            name="Typhoon precipitation probability",
            model_type=ModelType.STATISTICAL,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Position-conditioned precipitation climatology over Taiwan. "
                "Produces exceedance probabilities and expected intensity per "
                "grid cell for each step along a track."
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.SIMULATION, ExecutionKind.PREDICTION),
            input_contract=Contract(
                shape=ContractShape.FREE,
                description="{'track': [{latitude, longitude, wind_kt?}, ...]}",
            ),
            parameter_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec("thresholds", FieldType.ARRAY, required=False,
                              unit="mm/hr",
                              description="rain-rate thresholds for exceedance"),
                    FieldSpec("bandwidth_km", FieldType.FLOAT, required=False,
                              default=DEFAULT_BANDWIDTH_KM, unit="km",
                              description="Gaussian kernel width over storm position"),
                    FieldSpec("frames", FieldType.INTEGER, required=False, default=12,
                              description="interpolated positions along the track"),
                    FieldSpec("use_wind", FieldType.BOOLEAN, required=False,
                              default=True,
                              description="weight analogs by intensity similarity"),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.OBJECT,
                description="grid definition plus one frame per interpolated position",
                fields=[
                    FieldSpec("grid_lat", FieldType.ARRAY),
                    FieldSpec("grid_lon", FieldType.ARRAY),
                    FieldSpec("frames", FieldType.ARRAY),
                    FieldSpec("thresholds", FieldType.ARRAY),
                ],
            ),
            examples=[
                {
                    "name": "Rain-rate exceedance for a landfalling track",
                    "description": (
                        "Probability that rainfall exceeds 10, 25 and 50 mm/hr "
                        "at each point along the forecast track, from the "
                        "historical analogs nearest to it."
                    ),
                    "configuration": {},
                    "parameters": {
                        "thresholds": [10, 25, 50],
                        "bandwidth_km": DEFAULT_BANDWIDTH_KM,
                        "frames": 12,
                        "use_wind": True,
                    },
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        if not is_available():
            result.add_warning(
                f"the analog database ({ANALOG_DB_FILENAME}) is not present; "
                "executions will fail until it is built"
            )
        config = definition.configuration or {}
        frames = config.get("frames", 12)
        if not isinstance(frames, int) or not 1 <= frames <= MAX_FRAMES:
            result.add_error(f"configuration.frames must be between 1 and {MAX_FRAMES}")
        thresholds = config.get("thresholds")
        if thresholds is not None and (
            not isinstance(thresholds, list) or not thresholds
        ):
            result.add_error("configuration.thresholds must be a non-empty list")
        return result

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        params = {**(context.definition.configuration or {}), **context.parameters}
        record = context.input.record or {}
        if not record.get("track") and not context.input.has_table:
            raise ValidationError("provide input.track with at least 2 points")

        points = record.get("track") or context.input.rows()
        frame = track_dataframe(points)
        track = [
            {"lat": float(row.latitude), "lon": float(row.longitude),
             "wind_kt": _optional_float(row.wind_kt)}
            for row in frame.itertuples()
        ]

        steps = int(params.get("frames", 12))
        if not 1 <= steps <= MAX_FRAMES:
            raise ValidationError(f"frames must be between 1 and {MAX_FRAMES}")
        thresholds = [float(t) for t in (params.get("thresholds") or DEFAULT_THRESHOLDS)]
        bandwidth = float(params.get("bandwidth_km", DEFAULT_BANDWIDTH_KM))

        captured = io.StringIO()
        with redirect_stdout(captured):
            model = get_model()
            positions = interpolate_track(track, steps)
            frames = model.forecast_positions(
                positions,
                thresholds=thresholds,
                bandwidth_km=bandwidth,
                use_wind=bool(params.get("use_wind", True)),
            )
        for line in captured.getvalue().splitlines():
            if line.strip():
                context.log(line.strip())
        context.log(f"estimated {len(frames)} frames over a {model.grid_shape} grid")

        value: dict[str, Any] = {
            "grid_lat": [round(float(v), 4) for v in model.grid_lat],
            "grid_lon": [round(float(v), 4) for v in model.grid_lon],
            "grid_shape": list(model.grid_shape),
            "thresholds": thresholds,
            "bandwidth_km": bandwidth,
            "frames": frames,
        }
        effective = [f["n_effective"] for f in frames if f.get("n_effective") is not None]
        return ExecutionOutcome(
            payload=ResultPayload(
                kind=ResultKind.PROBABILITY,
                value=value,
                summary={
                    "frames": len(frames),
                    "grid_shape": list(model.grid_shape),
                    "thresholds": thresholds,
                },
            ),
            metrics={
                "frames": len(frames),
                "mean_effective_analogs": (
                    round(sum(effective) / len(effective), 1) if effective else None
                ),
            },
            logs=context.logs,
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result  # drop NaN
