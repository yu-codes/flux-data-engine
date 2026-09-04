"""The built-in maintenance application: its own routes, contributed as a plugin.

These endpoints shape what the fleet page needs out of tables the platform
already produced. They are not a side door: the assessment they serve is the
same `Engine` the `asset-condition-decision` model runs, reading the same
feature dataset, and the page's "重新評估" submits an ordinary Execution that
lands in Executions and Results like anything else.

The one thing this file adds is *shape*. A fleet list, an asset's own history
and a chart of one measurement against its moving limit are three different
projections of the same assessment, and asking the browser to assemble them
from a generic table endpoint would mean shipping twenty thousand rows to draw
one line.

Reading is cached on the dataset **version**, which is immutable — so the cache
cannot go stale, only cold. A new pipeline run publishes a new version and the
next request builds against it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import Field

from app.api.deps import (
    DatasetServiceDep,
    ExecutionServiceDep,
    ModelServiceDep,
    ResultServiceDep,
)
from app.api.schema_base import ApiModel
from app.plugins.python_function.columnar import as_datetime, as_number
from app.shared.errors import NotFoundError
from app.shared.tabular import Table

from . import features as F
from .catalogue import CLASSES
from .engine import (
    DEFAULT_POLICY,
    POLICIES,
    Assessment,
    Engine,
    analyzer_catalogue,
    policy_catalogue,
)

router = APIRouter(
    prefix="/applications/asset-maintenance", tags=["application: asset maintenance"]
)

#  Resolved by name rather than by slug: `slugify` has no ascii to work with
#  in a Chinese name and falls back to a random suffix, so a slug written here
#  would match a different model on every install.
DECISION_MODEL_NAME = "設備維護決策"

#  Built per dataset version, which never changes once written. Two entries:
#  the current one, and the one a request that started before a re-run is
#  still finishing against.
_CACHE: dict[str, Engine] = {}
_CACHE_ORDER: list[str] = []
_CACHE_SIZE = 2


class AssessRequest(ApiModel):
    policy: str = Field(default=DEFAULT_POLICY)
    as_of: str | None = None
    asset_id: str | None = None


# --------------------------------------------------------------------------
# building the engine
# --------------------------------------------------------------------------
def _table(datasets, name: str, *, required: bool = True) -> Table | None:
    dataset = datasets.datasets.get_by_name(name)
    if dataset is None or not dataset.current_version_id:
        if required:
            raise NotFoundError(
                f"this application needs the '{name}' dataset. Run the "
                f"maintenance pipelines, or re-seed the workspace."
            )
        return None
    return datasets.read_table(dataset.current_version_id)


def _engine(datasets) -> Engine:
    """The assessment engine for the current feature version."""
    features = datasets.datasets.get_by_name(F.DAILY_FEATURES)
    if features is None or not features.current_version_id:
        raise NotFoundError(
            f"this application needs the '{F.DAILY_FEATURES}' dataset, which is "
            f"produced by the '{F.FEATURES_PIPELINE}' pipeline."
        )
    key = str(features.current_version_id)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    engine = Engine(
        features=datasets.read_table(key),
        assets=_table(datasets, F.ASSETS_DATASET),
        policies=_table(datasets, F.POLICY_DATASET, required=False),
        rules=_table(datasets, F.RULES_DATASET, required=False),
        maintenance=_table(datasets, F.MAINTENANCE_DATASET, required=False),
        failures=_table(datasets, F.FAILURE_DATASET, required=False),
        quality=_table(datasets, F.QUALITY_DATASET, required=False),
    )
    _CACHE[key] = engine
    _CACHE_ORDER.append(key)
    while len(_CACHE_ORDER) > _CACHE_SIZE:
        _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return engine


def _latest_day(datasets) -> str:
    """The most recent day in the feature table, read as one column."""
    features = datasets.datasets.get_by_name(F.DAILY_FEATURES)
    if features is None or not features.current_version_id:
        raise NotFoundError(
            f"this application needs the '{F.DAILY_FEATURES}' dataset, which is "
            f"produced by the '{F.FEATURES_PIPELINE}' pipeline."
        )
    days = datasets.read_table(
        features.current_version_id, columns=[F.C_DAY]
    ).column_values(F.C_DAY)
    return max((str(day) for day in days), default="")


def _moment(engine: Engine, as_of: str | None) -> datetime:
    return (as_datetime(as_of) if as_of else None) or (
        as_datetime(engine.latest_day) or datetime.now()
    )


def _policy(name: str | None) -> str:
    return name if name in POLICIES else DEFAULT_POLICY


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------
@router.get("/catalogue", summary="Analyzers, policies and equipment classes")
def catalogue(datasets: DatasetServiceDep):
    #  Deliberately does not build the engine. This is the first request the
    #  page makes, and building the engine to answer it meant every cold page
    #  load waited three seconds for a list of analyzer names that never
    #  change. The one thing here that does change is the latest day, and that
    #  is one column of one dataset.
    return {
        "analyzers": analyzer_catalogue(),
        "policies": policy_catalogue(),
        "default_policy": DEFAULT_POLICY,
        "latest_day": _latest_day(datasets),
        "asset_types": [
            {
                "key": klass.key,
                "label": klass.label,
                "measurements": [
                    {"parameter": m.parameter, "label": m.label, "unit": m.unit,
                     "direction": m.direction}
                    for m in klass.measurements
                ],
                "failure_modes": [
                    {"key": mode.key, "label": mode.label, "symptom": mode.symptom,
                     "root_cause": mode.root_cause, "action": mode.action,
                     "severity": mode.severity}
                    for mode in klass.modes
                ],
                "policies": [
                    {"task": task, "interval_hours": hours, "kind": kind}
                    for task, hours, kind in klass.usage_policy
                ]
                + [
                    {"task": task, "interval_days": days, "kind": kind}
                    for task, days, kind in klass.time_policy
                ],
            }
            for klass in CLASSES.values()
        ],
    }


# --------------------------------------------------------------------------
# the fleet
# --------------------------------------------------------------------------
@router.get("/fleet", summary="Every asset, assessed")
def fleet(
    datasets: DatasetServiceDep,
    policy: str = Query(DEFAULT_POLICY),
    as_of: str | None = Query(None),
    site: str | None = Query(None),
    criticality: str | None = Query(None),
    status: str | None = Query(None),
    required_only: bool = Query(False),
    search: str | None = Query(None),
):
    engine = _engine(datasets)
    chosen = _policy(policy)
    moment = _moment(engine, as_of)
    rows: list[dict[str, Any]] = []
    for asset in engine.assets:
        assessment = engine.assess(asset, as_of=moment, policy=chosen)
        if assessment is None:
            continue
        row = assessment.decision()
        row["measurements"] = len(assessment.measurements)
        row["worst_measurement"] = _worst(assessment)
        rows.append(row)

    filtered = [
        row for row in rows
        if (not site or row.get("site_id") == site)
        and (not criticality or row.get("criticality") == criticality)
        and (not status or row.get("health_status") == status)
        and (not required_only or row.get("maintenance_required"))
        and (
            not search
            or search.lower() in f"{row.get('asset_id')} {row.get('asset_name')}".lower()
        )
    ]
    order = {"IMMEDIATE": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "NONE": 4}
    filtered.sort(
        key=lambda row: (
            order.get(str(row.get("priority")), 5),
            row.get("health_score") if row.get("health_score") is not None else 999,
        )
    )
    return {
        "as_of": moment.strftime("%Y-%m-%d"),
        "policy": chosen,
        "policy_label": POLICIES[chosen].label,
        "total": len(rows),
        "summary": _summary(rows),
        "sites": sorted({str(row.get("site_id")) for row in rows if row.get("site_id")}),
        "assets": filtered,
    }


def _worst(assessment: Assessment) -> dict[str, Any] | None:
    """The measurement carrying the assessment, for the list row."""
    ranked = [
        item for item in assessment.measurements if item.limit_progress_pct is not None
    ]
    if not ranked:
        return None
    item = max(ranked, key=lambda m: m.limit_progress_pct or 0)
    return {
        "parameter": item.parameter,
        "label": item.label,
        "unit": item.unit,
        "value": item.value,
        "expected": item.expected,
        "limit_progress_pct": item.limit_progress_pct,
        "status": item.status,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r["health_score"] for r in rows if r.get("health_score") is not None]
    return {
        "assets": len(rows),
        "maintenance_required": sum(1 for r in rows if r.get("maintenance_required")),
        "by_risk": _count(rows, "risk_level"),
        "by_health": _count(rows, "health_status"),
        "by_priority": _count(rows, "priority"),
        "by_criticality": _count(rows, "criticality"),
        "mean_health": round(sum(scored) / len(scored), 1) if scored else None,
        "worst_health": min(scored) if scored else None,
        "suspect_data": sum(
            1 for r in rows if r.get("data_quality_flag") in ("suspect", "bad")
        ),
    }


def _count(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        out[value] = out.get(value, 0) + 1
    return out


# --------------------------------------------------------------------------
# one asset
# --------------------------------------------------------------------------
@router.get("/assets/{asset_id}", summary="One asset: decision, evidence and history")
def asset_detail(
    asset_id: str,
    datasets: DatasetServiceDep,
    policy: str = Query(DEFAULT_POLICY),
    as_of: str | None = Query(None),
):
    engine = _engine(datasets)
    chosen = _policy(policy)
    moment = _moment(engine, as_of)
    asset = next(
        (a for a in engine.assets if str(a.get("asset_id")) == asset_id), None
    )
    if asset is None:
        raise NotFoundError(f"no asset '{asset_id}' in the asset register")
    assessment = engine.assess(asset, as_of=moment, policy=chosen)
    if assessment is None:
        return {
            "asset": asset,
            "as_of": moment.strftime("%Y-%m-%d"),
            "policy": chosen,
            "decision": None,
            "message": "此日期之前沒有可用的量測資料",
        }

    view = engine.view(asset, moment)
    return {
        "asset": asset,
        "as_of": moment.strftime("%Y-%m-%d"),
        "policy": chosen,
        "policy_label": POLICIES[chosen].label,
        "decision": assessment.decision(),
        "health": {
            "score": assessment.health_score,
            "status": assessment.health_status,
            "coverage": assessment.health_coverage,
            "components": assessment.health_components,
        },
        "risk": {
            "level": assessment.risk_level,
            "likelihood": assessment.likelihood,
            "consequence": assessment.asset.get("criticality"),
            "basis": assessment.risk_basis,
            "concern": round(assessment.concern, 2),
        },
        "window": {
            "start": assessment.window_start,
            "end": assessment.window_end,
            "basis": assessment.window_basis,
            "reason": assessment.window_reason,
        },
        "measurements": [item.to_dict() for item in assessment.measurements],
        "evidence": [
            {k: v for k, v in row.items() if k != "detail"}
            for row in assessment.evidence_rows()
        ],
        "data_quality": assessment.data_quality,
        "features": {
            k: v for k, v in assessment.record.items() if not k.startswith("_")
        },
        "policies": view.policies if view else [],
        "maintenance": sorted(
            (view.maintenance if view else []),
            key=lambda event: str(event.get("maintenance_date")),
            reverse=True,
        )[:25],
        "failures": sorted(
            (view.failures if view else []),
            key=lambda event: str(event.get("failure_date")),
            reverse=True,
        )[:15],
        "timeline": _timeline(assessment, view),
    }


def _timeline(assessment: Assessment, view) -> list[dict[str, Any]]:
    """Telemetry → condition → rule → risk → recommendation, in one sequence.

    Assembled here rather than in the browser because the ordering rule is a
    judgement — a failure and the repair that answered it belong next to each
    other even when a routine inspection happened between them — and a
    judgement belongs where it can be read.
    """
    entries: list[dict[str, Any]] = []
    for event in view.failures if view else []:
        entries.append(
            {
                "at": str(event.get("failure_date"))[:10],
                "kind": "failure",
                "title": f"故障：{event.get('failure_mode') or event.get('failure_type')}",
                "detail": str(event.get("symptoms") or ""),
                "severity": str(event.get("severity") or ""),
                "extra": {
                    "downtime_hours": event.get("downtime_hours"),
                    "impact": event.get("production_impact"),
                    "detected_by": event.get("detected_by"),
                },
            }
        )
    for event in view.maintenance if view else []:
        entries.append(
            {
                "at": str(event.get("maintenance_date"))[:10],
                "kind": str(event.get("maintenance_type") or "maintenance"),
                "title": f"{_maintenance_zh(event)}：{event.get('task')}",
                "detail": str(event.get("action") or ""),
                "severity": "",
                "extra": {
                    "downtime_hours": event.get("downtime_hours"),
                    "cost": event.get("cost"),
                    "parts": event.get("parts_replaced"),
                    "technician": event.get("technician"),
                    "result": event.get("result"),
                },
            }
        )
    #  The current assessment closes the sequence: this is what the record
    #  above adds up to today.
    entries.append(
        {
            "at": assessment.as_of.strftime("%Y-%m-%d"),
            "kind": "assessment",
            "title": (
                f"評估：{assessment.health_status}"
                f"（健康 {_shown(assessment.health_score)}）"
            ),
            "detail": assessment.recommended_action,
            "severity": str(assessment.risk_level or ""),
            "extra": {
                "risk_level": assessment.risk_level,
                "priority": assessment.priority,
                "window_start": assessment.window_start,
                "window_end": assessment.window_end,
                "window_basis": assessment.window_basis,
                "confidence": assessment.confidence,
                "reasons": assessment.reasons,
            },
        }
    )
    return sorted(entries, key=lambda entry: entry["at"], reverse=True)


def _leading(rows: list[dict[str, Any]], available: list[str]) -> str:
    """The measurement furthest towards its limit, on its most recent day."""
    latest: dict[str, tuple[str, float]] = {}
    for row in rows:
        name = str(row.get(F.C_PARAMETER))
        day = str(row.get(F.C_DAY))
        progress = as_number(row.get(F.C_LIMIT_PROGRESS))
        if progress is None:
            continue
        seen = latest.get(name)
        if seen is None or day > seen[0]:
            latest[name] = (day, progress)
    if not latest:
        return available[0]
    return max(latest.items(), key=lambda item: item[1][1])[0]


def _shown(value: float | None) -> str:
    """A number, or an em dash. Written once so a template stays readable."""
    return "—" if value is None else f"{value:g}"


_MAINTENANCE_ZH = {
    "preventive": "預防保養",
    "corrective": "矯正維修",
    "inspection": "定期檢查",
    "predictive": "預知保養",
    "emergency": "緊急搶修",
}


def _maintenance_zh(event: dict[str, Any]) -> str:
    return _MAINTENANCE_ZH.get(str(event.get("maintenance_type")), "維護")


# --------------------------------------------------------------------------
# one measurement over time
# --------------------------------------------------------------------------
@router.get("/assets/{asset_id}/series", summary="A measurement against its moving limit")
def asset_series(
    asset_id: str,
    datasets: DatasetServiceDep,
    parameter: str | None = Query(None),
    days: int = Query(90, ge=7, le=400),
    as_of: str | None = Query(None),
):
    engine = _engine(datasets)
    moment = _moment(engine, as_of)
    rows = engine.by_asset.get(asset_id, [])
    if not rows:
        raise NotFoundError(f"no readings for asset '{asset_id}'")

    available = sorted({str(row.get(F.C_PARAMETER)) for row in rows})
    #  Default to the measurement that is carrying the assessment rather than
    #  to whichever one sorts first. Opening an asset that was flagged for a
    #  rising vibration on a chart of its condenser pressure is a page that
    #  answers a question nobody asked.
    wanted = parameter or _leading(rows, available)
    if wanted not in available:
        raise NotFoundError(
            f"asset '{asset_id}' has no measurement '{wanted}'",
        )
    floor = (moment - timedelta(days=days)).strftime("%Y-%m-%d")
    cutoff = moment.strftime("%Y-%m-%d")
    series = [
        row for row in rows
        if str(row.get(F.C_PARAMETER)) == wanted
        and floor <= str(row.get(F.C_DAY)) <= cutoff
    ]

    view_asset = next(
        (a for a in engine.assets if str(a.get("asset_id")) == asset_id), {}
    )
    events = [
        {
            "at": str(event.get("maintenance_date"))[:10],
            "kind": str(event.get("maintenance_type")),
            "label": str(event.get("task") or ""),
        }
        for event in engine.maintenance.get(asset_id, [])
        if floor <= str(event.get("maintenance_date"))[:10] <= cutoff
    ] + [
        {
            "at": str(event.get("failure_date"))[:10],
            "kind": "failure",
            "label": str(event.get("failure_mode") or ""),
        }
        for event in engine.failures.get(asset_id, [])
        if floor <= str(event.get("failure_date"))[:10] <= cutoff
    ]

    return {
        "asset_id": asset_id,
        "asset_name": view_asset.get("asset_name"),
        "parameter": wanted,
        "parameter_label": (
            str(series[0].get("parameter_label")) if series else wanted
        ),
        "unit": str(series[0].get(F.C_UNIT)) if series else "",
        "direction": str(series[0].get("direction")) if series else "high",
        "available": [
            {
                "parameter": name,
                "label": next(
                    (str(row.get("parameter_label")) for row in rows
                     if str(row.get(F.C_PARAMETER)) == name),
                    name,
                ),
            }
            for name in available
        ],
        "points": [
            {
                "day": str(row.get(F.C_DAY)),
                "value": as_number(row.get(F.C_MEAN)),
                "high": as_number(row.get(F.C_MAX)),
                "low": as_number(row.get(F.C_MIN)),
                "expected": as_number(row.get(F.C_EXPECTED)),
                "warning": as_number(row.get("warning_value")),
                "critical": as_number(row.get("critical_value")),
                "emergency": as_number(row.get("emergency_value")),
                "status": str(row.get(F.C_STATUS) or "normal"),
                "progress": as_number(row.get(F.C_LIMIT_PROGRESS)),
                "load_pct": as_number(row.get(F.C_LOAD)),
                "ambient_c": as_number(row.get(F.C_AMBIENT)),
                "samples": as_number(row.get(F.C_SAMPLES)),
            }
            for row in series
        ],
        "events": sorted(events, key=lambda event: event["at"]),
    }


# --------------------------------------------------------------------------
# re-assessing, as a platform execution
# --------------------------------------------------------------------------
@router.post("/assess", summary="Re-run the assessment and record it")
def assess(
    payload: AssessRequest,
    models: ModelServiceDep,
    executions: ExecutionServiceDep,
    results: ResultServiceDep,
):
    """Submit the assessment as an ordinary Execution.

    The read endpoints above answer from the current feature version without
    recording anything, because a page load is not an event worth keeping. A
    deliberate re-assessment is, and it goes through the same path as every
    other model run so it appears in Executions, produces a Result, and can be
    traced back to the dataset version it read.
    """
    model = models.repository.get_by_name(DECISION_MODEL_NAME)
    if model is None:
        raise NotFoundError(
            f"the '{DECISION_MODEL_NAME}' model is not in this workspace"
        )
    execution = executions.submit(
        model_id=model.id,
        kind="calculation",
        parameters={
            "policy": _policy(payload.policy),
            "as_of": payload.as_of,
            "asset_id": payload.asset_id,
        },
    )
    result = results.for_execution(execution.id)
    return {
        "execution_id": execution.id,
        "status": execution.status.value,
        "metrics": execution.metrics,
        "result_id": result.id if result else None,
        "summary": result.summary if result else {},
        "rows": (
            results.read_payload(result.id, limit=200) if result else []
        ),
    }
