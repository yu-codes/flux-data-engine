"""Built-in typhoon application: its own routes, contributed as a plugin.

These endpoints exist so the map UI can stay simple, but they are not a side
door: every forecast is submitted as a normal Execution against the seeded
typhoon models, and therefore lands in Executions and Results like any other
run. The application layer only shapes the request and unwraps the payload.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import Field

from app.api.deps import ExecutionServiceDep, ResultServiceDep
from app.api.schema_base import ApiModel
from app.plugins.typhoon_analog import precip_plugin
from app.plugins.typhoon_analog.algorithms.regions import RAINFALL_REGIONS
from app.plugins.typhoon_analog.engine import (
    DEFAULT_BUFFER_KM,
    DEFAULT_METHOD,
    METHODS,
    category_catalogue,
    coastline_geometry,
    get_engine,
)
from app.shared.errors import ExecutionError

router = APIRouter(prefix="/applications/typhoon", tags=["application: typhoon"])

#  Slugs of the models seeded for this application.
ANALOG_MODEL_SLUG = "typhoon-analog"
PRECIP_MODEL_SLUG = "typhoon-precipitation-probability"


class TrackPoint(ApiModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=360)
    wind_kt: float | None = None
    pressure_mb: float | None = None
    timestamp_utc: str | None = None


class PredictRequest(ApiModel):
    track: list[TrackPoint] = Field(default_factory=list, min_length=0)
    typhoon_id: str | None = None
    method: str = DEFAULT_METHOD
    k: int = Field(default=5, ge=1, le=20)
    buffer_km: float = Field(default=DEFAULT_BUFFER_KM, ge=50, le=2000)
    use_rainfall: bool = False
    rainfall_region: str = "tn"
    rainfall_weight: float | None = None
    expected_rainfall: float | None = None


class PrecipRequest(ApiModel):
    track: list[TrackPoint] = Field(min_length=2)
    frames: int = Field(default=12, ge=1, le=60)
    bandwidth_km: float = Field(default=150.0, ge=25, le=600)
    thresholds: list[float] | None = None
    use_wind: bool = True


# --------------------------------------------------------------------------
# catalogue / static geometry
# --------------------------------------------------------------------------
@router.get("/methods", summary="Available similarity methods")
def list_methods():
    return {
        "default": DEFAULT_METHOD,
        "methods": [
            {"key": key, "description": text} for key, text in METHODS.items()
        ],
        "rainfall_regions": [
            {"code": code, "label": meta["label"]}
            for code, meta in RAINFALL_REGIONS.items()
        ],
        "precipitation_available": precip_plugin.is_available(),
    }


@router.get("/categories", summary="CWA landfall-track categories")
def list_categories():
    return {"categories": category_catalogue()}


@router.get("/coastline", summary="Coastline outline and the buffer polygon")
def get_coastline(buffer_km: float = Query(DEFAULT_BUFFER_KM, ge=50, le=2000)):
    return coastline_geometry(buffer_km)


@router.get("/typhoons", summary="Historical typhoons available as analogs")
def list_typhoons(
    search: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    engine = get_engine()
    records = engine.loader.records
    if category:
        records = [r for r in records if r.taiwan_track_category == category]
    if search:
        needle = search.lower()
        records = [
            r
            for r in records
            if needle in (r.name_zh or "").lower()
            or needle in (r.name_en or "").lower()
            or needle in r.typhoon_id.lower()
        ]
    total = len(records)
    return {
        "total": total,
        "typhoons": [
            {
                "typhoon_id": r.typhoon_id,
                "year": r.year,
                "name_zh": r.name_zh,
                "name_en": r.name_en,
                "category": r.taiwan_track_category,
                "landfall_location": r.landfall_location,
                "track_points": len(r.track),
                "event_rain": r.event_rain,
            }
            for r in sorted(records, key=lambda r: r.year, reverse=True)[:limit]
        ],
    }


@router.get("/typhoons/{typhoon_id}/track", summary="One historical typhoon's track")
def get_typhoon_track(
    typhoon_id: str, buffer_km: float = Query(DEFAULT_BUFFER_KM, ge=50, le=2000)
):
    from app.plugins.typhoon_analog.engine import track_coords

    engine = get_engine(buffer_km=buffer_km)
    try:
        record = engine.loader.get(typhoon_id)
    except KeyError as exc:
        raise ExecutionError(f"unknown typhoon '{typhoon_id}'") from exc
    return {
        "typhoon_id": record.typhoon_id,
        "name_zh": record.name_zh,
        "name_en": record.name_en,
        "year": record.year,
        "category": record.taiwan_track_category,
        "track": track_coords(record.track, buffer_km),
    }


# --------------------------------------------------------------------------
# forecasting - submitted as platform executions
# --------------------------------------------------------------------------
@router.post("/predict", summary="Find analogs and vote on a track category")
def predict(
    payload: PredictRequest,
    executions: ExecutionServiceDep,
    results: ResultServiceDep,
):
    input_payload: dict[str, Any] = {}
    if payload.typhoon_id:
        input_payload["typhoon_id"] = payload.typhoon_id
    else:
        input_payload["track"] = [p.model_dump() for p in payload.track]

    execution = executions.submit(
        model_id=ANALOG_MODEL_SLUG,
        kind="prediction",
        input_payload=input_payload,
        parameters={
            "method": payload.method,
            "k": payload.k,
            "buffer_km": payload.buffer_km,
            "use_rainfall": payload.use_rainfall,
            "rainfall_region": payload.rainfall_region,
            "rainfall_weight": payload.rainfall_weight,
            "expected_rainfall": payload.expected_rainfall,
        },
    )
    return _unwrap(execution, results)


@router.post("/precipitation", summary="Precipitation probability along a track")
def precipitation(
    payload: PrecipRequest,
    executions: ExecutionServiceDep,
    results: ResultServiceDep,
):
    execution = executions.submit(
        model_id=PRECIP_MODEL_SLUG,
        kind="simulation",
        input_payload={"track": [p.model_dump() for p in payload.track]},
        parameters={
            "frames": payload.frames,
            "bandwidth_km": payload.bandwidth_km,
            "thresholds": payload.thresholds,
            "use_wind": payload.use_wind,
        },
    )
    return _unwrap(execution, results)


def _unwrap(execution, results: ResultServiceDep) -> dict[str, Any]:
    """Return the result payload plus the execution record that produced it."""
    if execution.error:
        raise ExecutionError(execution.error, details={"execution_id": execution.id})
    payload = results.read_payload(execution.result_id) if execution.result_id else None
    return {
        "execution_id": execution.id,
        "result_id": execution.result_id,
        "status": execution.status.value,
        "metrics": execution.metrics,
        "duration_seconds": execution.duration_seconds,
        "logs": execution.logs,
        **(payload if isinstance(payload, dict) else {"payload": payload}),
    }
