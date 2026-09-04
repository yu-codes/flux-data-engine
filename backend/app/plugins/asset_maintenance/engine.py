"""The analysis engine: evidence in, a maintenance decision out.

No machine learning, and not because of a restriction — because for this
question the layers below actually answer it, and each one can say why:

    readings
      → threshold      is it past a line somebody signed off?
      → baseline       is it away from what the physics says it should be at
                       this load in this plant room?
      → trend          is it moving, and is the movement a trend or noise?
      → statistics     is the movement large compared with this asset's own
                       normal variation?
      → signature      do several measurements move in the pattern a known
                       failure mode produces?
      → runtime        has it consumed its maintenance interval?
      → history        has this asset, or this class, done this before?
      → engineering    do any of the declared rules fire?
      → quality        can the readings be trusted at all?
                              ↓
                     health assessment  (weighted, coverage-aware)
                              ↓
                       risk assessment  (likelihood × consequence)
                              ↓
                     maintenance window (projection, with a stated basis)
                              ↓
                    maintenance decision (thresholds set by criticality)

Three design decisions are worth stating, because each is a place the obvious
implementation is wrong.

**Analyzers are registered, not called in sequence.** `ANALYZERS` is a list;
adding one is appending to it, and a *policy* is a set of their keys. That is
what makes the four decision policies comparable in an Experiment: they are the
same engine with different analyzers enabled, so a difference in the score is a
difference in the analysis rather than in the code path.

**A finding may argue against acting.** Weights are signed. A data-quality
finding subtracts, because an instrument that is stuck is a reason to doubt
the reading that crossed the line, not a reason to send somebody to the
machine. A system that can only accumulate concern will eventually flag
everything.

**The decision threshold depends on criticality.** A transformer whose failure
stops the site and a fan that does not are not owed the same evidence before
somebody goes and looks. Using one threshold for both means choosing which of
the two to get wrong.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.plugins.formula.expression import compile_expression, evaluate
from app.plugins.projection.projecting import Limit, Projection, fit_series
from app.plugins.python_function.columnar import as_datetime, as_number
from app.plugins.risk_matrix.matrix import matrix_from_config
from app.plugins.scorecard.scoring import scorecard_from_config
from app.shared.tabular import Table

from . import features as F
from .catalogue import CLASSES

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------
HEALTH_BANDS = (
    (85.0, "HEALTHY"),
    (70.0, "WATCH"),
    (50.0, "DEGRADED"),
    (30.0, "POOR"),
)
HEALTH_WORST = "CRITICAL"

#  A deviation smaller than this is inside the noise of an ordinary plant and
#  is not evidence of anything. Expressed as a share of the distance from
#  "exactly as predicted" to the emergency limit, which is the only scale on
#  which two different measurements can be compared at all.
SIGNIFICANT_PROGRESS_PCT = 22.0
#  Kept for the per-parameter engineering rules, which compare a measurement
#  against itself and for which a percentage of the expected value is the
#  natural unit.
SIGNIFICANT_DEVIATION_PCT = 8.0

#  How much evidence is enough before somebody is sent to look, by how much it
#  costs to be wrong about this asset. The spread is the point: a critical
#  asset is inspected on weaker evidence because the alternative is worse.
CONCERN_THRESHOLD = {
    "critical": 26.0,
    "high": 34.0,
    "medium": 44.0,
    "low": 56.0,
}
DEFAULT_CONCERN_THRESHOLD = 44.0

RISK_MATRIX = {
    "likelihood": {
        "column": "likelihood_score",
        "levels": ["low", "medium", "high"],
        "bands": [30.0, 60.0],
        "default": "low",
    },
    "consequence": {
        "column": "criticality",
        "levels": ["low", "medium", "high", "critical"],
        "default": "medium",
    },
    "grid": [
        ["LOW", "LOW", "MEDIUM", "HIGH"],
        ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        ["MEDIUM", "HIGH", "CRITICAL", "CRITICAL"],
    ],
    "severity_order": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
}

#  The health scorecard. Written as configuration rather than as an expression
#  so that every component states what its absence means and the score reports
#  how much of itself it could actually measure.
HEALTH_SCORECARD = {
    "components": [
        {
            "name": "門檻餘裕", "column": "worst_limit_progress_pct", "weight": 3.0,
            "kind": "linear", "good": 0.0, "bad": 100.0, "missing": "skip",
            "description": "最接近緊急門檻的量測，距離參考值多遠",
        },
        {
            "name": "基線偏離", "column": "worst_deviation_pct", "weight": 2.5,
            "kind": "linear", "good": 0.0, "bad": 45.0, "missing": "skip",
            "description": "扣掉負載與環境後，仍偏離應有值多少",
        },
        {
            "name": "劣化趨勢", "column": "worst_trend_slope_per_day", "weight": 2.5,
            "kind": "linear", "good": 0.0, "bad": 1.5, "missing": "neutral",
            "neutral_score": 75.0,
            "description": "每天往緊急門檻推進幾個百分點",
        },
        {
            "name": "波動增加", "column": "variability_ratio", "weight": 1.0,
            "kind": "linear", "good": 1.0, "bad": 3.0, "missing": "skip",
            "description": "近期離散度相對於自身基線的倍數",
        },
        {
            "name": "保養週期消耗", "column": "interval_usage_pct", "weight": 1.5,
            "kind": "linear", "good": 0.0, "bad": 120.0, "missing": "skip",
            "description": "累積運轉時數佔建議保養週期的比例",
        },
        {
            "name": "歷史故障", "column": "recent_failure_score", "weight": 1.0,
            "kind": "linear", "good": 0.0, "bad": 100.0, "missing": "neutral",
            "neutral_score": 85.0,
            "description": "距上次矯正性維修的時間相對於 MTBF",
        },
        {
            "name": "資料品質", "column": "min_quality_score", "weight": 1.5,
            "kind": "passthrough", "missing": "neutral", "neutral_score": 60.0,
            "description": "最差的一條量測序列的資料品質分數",
        },
    ],
    "bands": [
        {"upto": 30, "label": HEALTH_WORST},
        {"upto": 50, "label": "POOR"},
        {"upto": 70, "label": "DEGRADED"},
        {"upto": 85, "label": "WATCH"},
        {"upto": None, "label": "HEALTHY"},
    ],
    #  Below a third of the evidence, no score is offered. A cold-start asset
    #  gets its manufacturer's policy, not a confident number about nothing.
    "min_coverage": 0.33,
}


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------
@dataclass
class Finding:
    """One piece of evidence, and what it argues for.

    `weight` is signed and on the same 0-100 scale as concern, so the sum is
    directly comparable with the decision thresholds above. `confidence` scales
    it: a finding nobody should act on alone contributes less, and says so.
    """

    analyzer: str
    category: str
    statement: str
    weight: float
    confidence: float = 0.7
    parameter: str | None = None
    failure_mode: str | None = None
    action: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def contribution(self) -> float:
        return self.weight * self.confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyzer": self.analyzer,
            "category": self.category,
            "statement": self.statement,
            "weight": round(self.weight, 2),
            "confidence": round(self.confidence, 3),
            "contribution": round(self.contribution, 2),
            "parameter": self.parameter,
            "failure_mode": self.failure_mode,
            "action": self.action,
            "detail": self.detail,
        }


@dataclass
class Measurement:
    """One measurement of one asset, as it stood on the assessment date."""

    parameter: str
    label: str
    unit: str
    day: str
    value: float | None
    expected: float | None
    deviation_pct: float | None
    limit_progress_pct: float | None
    progress_slope: float | None
    progress_fit: float | None
    status: str
    rank: int
    residual: float | None
    residual_spread: float | None
    baseline_spread: float | None
    residual_z: float | None
    warning: float | None
    critical: float | None
    emergency: float | None
    direction: str
    samples: int
    quality_score: float | None = None
    quality_issues: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter": self.parameter,
            "parameter_label": self.label,
            "unit": self.unit,
            "day": self.day,
            "value": self.value,
            "expected": self.expected,
            "deviation_pct": self.deviation_pct,
            "limit_progress_pct": self.limit_progress_pct,
            "trend_per_day": self.progress_slope,
            "trend_fit": self.progress_fit,
            "status": self.status,
            "rank": self.rank,
            "warning": self.warning,
            "critical": self.critical,
            "emergency": self.emergency,
            "direction": self.direction,
            "quality_score": self.quality_score,
            "quality_issues": self.quality_issues,
        }


@dataclass
class AssetView:
    """Everything known about one asset on one date."""

    asset: dict[str, Any]
    as_of: datetime
    measurements: list[Measurement]
    #  Every daily row for this asset up to `as_of`, by parameter, oldest first.
    history: dict[str, list[dict[str, Any]]]
    policies: list[dict[str, Any]]
    maintenance: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    class_failures: dict[str, int]
    observed_days: int
    runtime_hours: float | None
    runtime_7d: float
    #  Days since the last corrective repair, if there has been one. The
    #  rolling windows in the feature table are computed over the whole series
    #  and know nothing about it, so a trend read four days after a bearing
    #  change is a trend fitted across the bearing change.
    days_since_repair: int | None = None
    record: dict[str, Any] = field(default_factory=dict)

    @property
    def trend_window_is_clean(self) -> bool:
        """Whether the trailing window lies entirely after the last repair."""
        return self.days_since_repair is None or self.days_since_repair >= F.LONG_WINDOW

    @property
    def asset_id(self) -> str:
        return str(self.asset.get("asset_id"))

    @property
    def asset_type(self) -> str:
        return str(self.asset.get("asset_type"))

    @property
    def criticality(self) -> str:
        return str(self.asset.get("criticality") or "medium")

    def measurement(self, parameter: str) -> Measurement | None:
        for item in self.measurements:
            if item.parameter == parameter:
                return item
        return None


# --------------------------------------------------------------------------
# the analyzers
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Analyzer:
    """One way of looking at an asset. Registered, never hard-wired."""

    key: str
    label: str
    category: str
    description: str
    run: Callable[[AssetView], list[Finding]]


def _threshold_analyzer(view: AssetView) -> list[Finding]:
    """Has anything crossed a line somebody signed off?"""
    findings: list[Finding] = []
    for item in view.measurements:
        if item.rank < 1:
            continue
        limit = {1: item.warning, 2: item.critical, 3: item.emergency}[item.rank]
        weight = {1: 18.0, 2: 34.0, 3: 52.0}[item.rank]
        findings.append(
            Finding(
                analyzer="threshold",
                category="threshold",
                statement=(
                    f"{item.label}達到{_status_zh(item.status)}門檻："
                    f"{_num(item.value)} {item.unit}"
                    f"（界線 {_num(limit)} {item.unit}）"
                ),
                weight=weight,
                #  A threshold is only as trustworthy as the reading that
                #  crossed it, so the instrument's own score scales it.
                confidence=0.95 * _quality_factor(item.quality_score),
                parameter=item.parameter,
                action=f"確認{item.label}並依門檻政策處置",
                detail={
                    "value": item.value,
                    "limit": limit,
                    "status": item.status,
                    "unit": item.unit,
                },
            )
        )
    return findings


def _baseline_analyzer(view: AssetView) -> list[Finding]:
    """Is it away from what this load and this plant room predict?

    Measured in `limit_progress_pct` rather than as a percentage of the
    expected value, because the latter is not comparable between measurements:
    a motor 37 rpm below where it should be is 2.5% of 1,480 and most of the
    way to its alarm, while a bearing 3 mm/s above where it should be is 111%
    of 2.7 and only half of the way to its own. Normalising by each
    measurement's own alarm band is what lets one number rank them.
    """
    findings: list[Finding] = []
    for item in view.measurements:
        progress = item.limit_progress_pct
        if progress is None or progress < SIGNIFICANT_PROGRESS_PCT:
            continue
        deviation = item.deviation_pct
        findings.append(
            Finding(
                analyzer="baseline",
                category="baseline",
                statement=(
                    f"{item.label}偏離同負載、同環境條件下的應有值："
                    f"實測 {_num(item.value)}，應為 {_num(item.expected)} {item.unit}"
                    + (f"（相差 {deviation:+.1f}%）" if deviation is not None else "")
                    + f"，已走完到緊急界線距離的 {progress:.0f}%"
                ),
                weight=min(26.0, 3.0 + progress * 0.28),
                confidence=0.75 * _quality_factor(item.quality_score),
                parameter=item.parameter,
                detail={
                    "limit_progress_pct": round(progress, 2),
                    "deviation_pct": None if deviation is None else round(deviation, 2),
                    "expected": item.expected,
                    "observed": item.value,
                    "load_pct": view.record.get("load_pct_mean"),
                    "ambient_c": view.record.get("ambient_temperature_c"),
                },
            )
        )
    return findings


def _rebuilt_window(view: AssetView) -> Finding:
    """The one finding a repaired machine gets instead of a trend."""
    return Finding(
        analyzer="quality",
        category="quality",
        statement=(
            f"{view.days_since_repair} 天前才完成矯正性維修，"
            f"{F.LONG_WINDOW} 天的趨勢視窗仍跨在維修之前，"
            f"此期間不以趨勢與統計判定"
        ),
        weight=-6.0,
        confidence=0.85,
        action="待趨勢視窗重建後再行判定",
        detail={"days_since_repair": view.days_since_repair,
                "window_days": F.LONG_WINDOW},
    )


def _trend_analyzer(view: AssetView) -> list[Finding]:
    """Is it moving towards the limit, and is the movement a trend?

    Suppressed while the trailing window still straddles a repair. A bearing
    change moves every signal it touches back to where it should be, and a
    slope fitted across that discontinuity reports the repair as degradation —
    which is how a machine that was just fixed becomes the one the system is
    most worried about.
    """
    if not view.trend_window_is_clean:
        return [_rebuilt_window(view)]
    findings: list[Finding] = []
    for item in view.measurements:
        slope = item.progress_slope
        if slope is None or slope <= 0.15:
            continue
        fit = item.progress_fit
        if fit is not None and fit < 0.25:
            #  Rising on average and not actually trending. Saying so is more
            #  useful than either flagging it or dropping it silently.
            findings.append(
                Finding(
                    analyzer="trend",
                    category="trend",
                    statement=(
                        f"{item.label}近 {F.LONG_WINDOW} 天平均在上升，"
                        f"但波動大於趨勢（R²={fit:.2f}），尚不足以視為劣化"
                    ),
                    weight=4.0,
                    confidence=0.4,
                    parameter=item.parameter,
                    detail={"slope_per_day": slope, "r_squared": fit},
                )
            )
            continue
        remaining = None
        if item.limit_progress_pct is not None and slope > 0:
            remaining = max(0.0, (100.0 - item.limit_progress_pct) / slope)
        findings.append(
            Finding(
                analyzer="trend",
                category="trend",
                statement=(
                    f"{item.label}持續上升：近 {F.LONG_WINDOW} 天每日推進 "
                    f"{slope:.2f}%（R²={fit:.2f}）"
                    + (f"，照此速率約 {remaining:.0f} 天到達緊急門檻"
                       if remaining is not None and remaining < 400 else "")
                ),
                weight=min(30.0, 6.0 + slope * 9.0),
                confidence=min(0.9, 0.45 + (fit or 0.5) * 0.5)
                * _quality_factor(item.quality_score),
                parameter=item.parameter,
                detail={
                    "slope_per_day": round(slope, 3),
                    "r_squared": fit,
                    "days_to_emergency": None if remaining is None else round(remaining, 1),
                },
            )
        )
    return findings


def _statistical_analyzer(view: AssetView) -> list[Finding]:
    """Is the movement large against this asset's own normal variation?"""
    if not view.trend_window_is_clean:
        #  Same discontinuity, and the trend analyzer has already said so.
        return []
    findings: list[Finding] = []
    for item in view.measurements:
        if item.residual_z is not None and abs(item.residual_z) >= 2.0:
            findings.append(
                Finding(
                    analyzer="statistical",
                    category="statistical",
                    statement=(
                        f"{item.label}目前偏離自身近 {F.LONG_WINDOW} 天分布 "
                        f"{item.residual_z:+.1f} 個標準差"
                    ),
                    weight=min(20.0, 6.0 * abs(item.residual_z) - 6.0),
                    confidence=0.7 * _quality_factor(item.quality_score),
                    parameter=item.parameter,
                    detail={"zscore": round(item.residual_z, 2)},
                )
            )
        spread, baseline = item.residual_spread, item.baseline_spread
        if spread and baseline and baseline > 1e-9:
            ratio = spread / baseline
            if ratio >= 1.8:
                findings.append(
                    Finding(
                        analyzer="statistical",
                        category="statistical",
                        statement=(
                            f"{item.label}的波動幅度是自身基線的 {ratio:.1f} 倍，"
                            f"運轉變得不穩定"
                        ),
                        weight=min(16.0, 4.0 * ratio),
                        confidence=0.6 * _quality_factor(item.quality_score),
                        parameter=item.parameter,
                        detail={"variability_ratio": round(ratio, 2)},
                    )
                )
    return findings


def _signature_analyzer(view: AssetView) -> list[Finding]:
    """Do several measurements move in the pattern a known mode produces?

    This is the analyzer a threshold system cannot have. A bearing raises
    vibration *and* bearing temperature *and* a little current; a blocked
    filter raises differential pressure and current and leaves vibration where
    it was. Both look like "something is high" one signal at a time, and are
    different repairs.
    """
    klass = CLASSES.get(view.asset_type)
    if klass is None:
        return []
    #  Keyed on limit progress rather than on percentage deviation, for the
    #  same reason the baseline analyzer is: a pattern across measurements can
    #  only be recognised if the measurements are on one scale.
    progress = {
        item.parameter: item.limit_progress_pct
        for item in view.measurements
        if item.limit_progress_pct is not None
    }
    if len(progress) < 2:
        return []

    findings: list[Finding] = []
    scored: list[tuple[float, Any, list[str]]] = []
    for mode in klass.modes:
        total = sum(abs(effect) for effect in mode.effects.values())
        if total <= 0:
            continue
        matched = 0.0
        supporting: list[str] = []
        for parameter, effect in mode.effects.items():
            measurement = klass.measurement(parameter)
            observed = progress.get(parameter)
            if measurement is None or observed is None:
                continue
            #  The direction the mode predicts, expressed in the oriented
            #  progress the pipeline produced: a mode that *lowers* a fail-low
            #  measurement and one that raises a fail-high one both predict
            #  positive progress.
            sign = 1.0 if (effect > 0) == (measurement.direction == "high") else -1.0
            hit = max(0.0, min(1.0, observed * sign / 50.0))
            matched += abs(effect) * hit
            if hit > 0.35:
                supporting.append(
                    f"{measurement.label}已達界線距離的 {observed:.0f}%"
                )
        share = matched / total
        #  Two moving measurements is the ordinary case for a pattern. One is
        #  allowed only when it carries most of the mode's weight on its own —
        #  a bearing early in its life moves vibration and almost nothing else,
        #  and refusing to name it until the temperature follows means naming
        #  it only once it is obvious.
        if share >= 0.45 and (len(supporting) >= 2 or share >= 0.6):
            scored.append((share, mode, supporting))

    for share, mode, supporting in sorted(scored, key=lambda item: -item[0])[:2]:
        findings.append(
            Finding(
                analyzer="signature",
                category="engineering",
                statement=(
                    f"多個量測同步變化，符合「{mode.label}」的典型型態"
                    f"（{'、'.join(supporting)}），比對度 {share:.0%}"
                ),
                weight=min(34.0, 12.0 + share * 26.0),
                confidence=min(0.85, 0.4 + share * 0.5),
                failure_mode=mode.key,
                action=mode.action,
                detail={
                    "mode": mode.key,
                    "mode_label": mode.label,
                    "match": round(share, 3),
                    "symptom": mode.symptom,
                    "root_cause": mode.root_cause,
                    "parts": mode.parts,
                    "supporting": supporting,
                },
            )
        )
    return findings


def _runtime_analyzer(view: AssetView) -> list[Finding]:
    """Has it consumed the interval its manufacturer publishes?"""
    findings: list[Finding] = []
    if view.runtime_hours is None:
        return findings
    unrecorded: list[str] = []
    for policy in view.policies:
        state = _interval_state(view, policy)
        if state is None:
            continue
        if not state["known"]:
            #  No record of the task ever being carried out. That is a gap in
            #  the maintenance record, not a machine that has run 240% of its
            #  interval — and reporting it as the latter was flagging every
            #  asset in the fleet for work nobody could justify.
            unrecorded.append(str(policy.get("task")))
            continue
        used = state["usage_pct"]
        if used < 70:
            continue
        findings.append(
            Finding(
                analyzer="runtime",
                category="policy",
                statement=(
                    f"「{policy.get('task')}」的建議週期為 "
                    f"{state['interval_hours']:,.0f} 運轉小時，"
                    f"自上次執行已累積 {state['hours_since']:,.0f} 小時（{used:.0f}%）"
                ),
                weight=6.0 if used < 90 else (16.0 if used < 100 else 26.0),
                confidence=0.9,
                action=str(policy.get("task")),
                detail={
                    "policy_id": policy.get("policy_id"),
                    "task": policy.get("task"),
                    "interval_hours": state["interval_hours"],
                    "hours_since": round(state["hours_since"], 1),
                    "usage_pct": round(used, 1),
                    "last_done": state["last_done"],
                },
            )
        )
    if unrecorded:
        findings.append(
            Finding(
                analyzer="runtime",
                category="policy",
                statement=(
                    f"維修紀錄中查無 {'、'.join(unrecorded[:3])}"
                    + ("等" if len(unrecorded) > 3 else "")
                    + " 的執行紀錄，無法計算週期消耗"
                ),
                weight=3.0,
                confidence=0.5,
                action="補齊保養紀錄或確認該項目是否已執行",
                detail={"unrecorded_tasks": unrecorded},
            )
        )
    return findings


def _calendar_analyzer(view: AssetView) -> list[Finding]:
    """And the intervals measured in days rather than in hours."""
    findings: list[Finding] = []
    for policy in view.policies:
        interval = as_number(policy.get("interval_days"))
        if not interval or interval <= 0:
            continue
        last = _last_task(view, policy)
        if last is None:
            #  Same rule as the usage intervals: an absent record is an absent
            #  record. Counting from commissioning turns a filing gap into a
            #  626%-overdue annual overhaul.
            continue
        days = (view.as_of - last).days
        used = 100.0 * days / interval
        if used < 80:
            continue
        findings.append(
            Finding(
                analyzer="calendar",
                category="policy",
                statement=(
                    f"「{policy.get('task')}」為每 {interval:,.0f} 天一次，"
                    f"上次執行至今 {days} 天（{used:.0f}%）"
                ),
                weight=4.0 if used < 100 else 12.0,
                confidence=0.9,
                action=str(policy.get("task")),
                detail={
                    "policy_id": policy.get("policy_id"),
                    "task": policy.get("task"),
                    "interval_days": interval,
                    "days_since": days,
                    "usage_pct": round(used, 1),
                },
            )
        )
    return findings


def _history_analyzer(view: AssetView) -> list[Finding]:
    """Has this asset, or this class of asset, done this before?"""
    findings: list[Finding] = []
    corrective = [
        event for event in view.maintenance
        if str(event.get("maintenance_type")) == "corrective"
    ]
    if corrective:
        latest = max(
            (as_datetime(event.get("maintenance_date")) for event in corrective),
            default=None,
        )
        if latest is not None:
            days = (view.as_of - latest).days
            #  Only once the window has been rebuilt. Before that, "it was
            #  repaired recently" is a reason the readings are still settling,
            #  not evidence that the repair failed — and raising concern on it
            #  put every freshly repaired machine back at the top of the list.
            if days <= 45 and view.trend_window_is_clean:
                findings.append(
                    Finding(
                        analyzer="history",
                        category="history",
                        statement=(
                            f"{days} 天前才剛做過矯正性維修（共 {len(corrective)} 次），"
                            f"趨勢視窗已重建但仍出現偏離，需檢討根本原因是否未解除"
                        ),
                        weight=10.0,
                        confidence=0.65,
                        detail={"days_since_corrective": days, "count": len(corrective)},
                    )
                )

    #  What the same signature has meant on this class of asset before. This is
    #  the historical comparison the doc asks for, and it is a lookup rather
    #  than a model: the record already says what these symptoms turned out to
    #  be.
    suspected = {mode for mode in view.record.get("_signature_modes", []) if mode}
    for mode in suspected:
        seen = view.class_failures.get(mode, 0)
        if seen <= 0:
            continue
        findings.append(
            Finding(
                analyzer="history",
                category="history",
                statement=(
                    f"同型設備歷史上共發生 {seen} 次「{_mode_label(view, mode)}」，"
                    f"目前的量測型態與那些個案一致"
                ),
                weight=min(14.0, 4.0 + seen * 1.5),
                confidence=0.6,
                failure_mode=mode,
                detail={"mode": mode, "historical_events": seen},
            )
        )
    return findings


def _engineering_analyzer(view: AssetView) -> list[Finding]:
    """Evaluate the declared engineering rules against the feature record.

    The rules are a dataset, so a plant engineer adds one without a deployment
    — which is the project's own requirement that this logic must not be
    compiled into the engine.
    """
    findings: list[Finding] = []
    record = view.record
    for rule in view.rules:
        scope = str(rule.get("asset_type") or "*")
        if scope not in ("*", view.asset_type):
            continue
        condition = str(rule.get("when") or "")
        if not condition:
            continue
        try:
            fired = bool(evaluate(compile_expression(condition), record))
        except Exception:
            #  A rule that names something this asset does not measure simply
            #  does not apply. It is not an error, and it must not stop the
            #  rules after it from being read.
            continue
        if not fired:
            continue
        findings.append(
            Finding(
                analyzer="engineering",
                category="engineering",
                statement=f"{rule.get('name')}：{rule.get('finding')}",
                weight=float(as_number(rule.get("weight")) or 0.0),
                confidence=float(as_number(rule.get("confidence")) or 0.7),
                failure_mode=rule.get("failure_mode"),
                action=rule.get("recommended_action"),
                detail={
                    "rule_id": rule.get("rule_id"),
                    "when": condition,
                    "source": rule.get("source"),
                },
            )
        )
    return findings


def _quality_analyzer(view: AssetView) -> list[Finding]:
    """Can the readings be trusted at all?

    The findings here are usually negative, and that is the point: an
    instrument that has been reporting the same number for four days is a
    reason to doubt the alarm, not a reason to raise it.
    """
    findings: list[Finding] = []
    for item in view.measurements:
        score = item.quality_score
        if score is None or score >= 70:
            continue
        crossing = item.rank >= 1 or (item.deviation_pct or 0) > SIGNIFICANT_DEVIATION_PCT
        findings.append(
            Finding(
                analyzer="quality",
                category="quality",
                statement=(
                    f"{item.label}的資料品質僅 {score:.0f} 分"
                    + (f"（{item.quality_issues}）" if item.quality_issues else "")
                    + ("，而它正是越線的量測——應先確認儀器" if crossing else "")
                ),
                #  Negative: it argues against acting on the equipment.
                weight=-(26.0 if crossing else 6.0),
                confidence=0.8,
                parameter=item.parameter,
                action="校驗或更換感測器後複判",
                detail={
                    "quality_score": score,
                    "issues": item.quality_issues,
                    "crossing": crossing,
                },
            )
        )
    if view.observed_days < 14:
        findings.append(
            Finding(
                analyzer="quality",
                category="quality",
                statement=(
                    f"僅有 {view.observed_days} 天的觀測資料，不足以建立設備自身基線；"
                    f"目前以製造商建議週期與設計限值為準"
                ),
                weight=-10.0,
                confidence=0.9,
                action="沿用製造商建議週期，待資料累積後改用自身基線",
                detail={"observed_days": view.observed_days, "cold_start": True},
            )
        )
    return findings


ANALYZERS: tuple[Analyzer, ...] = (
    Analyzer("threshold", "門檻分析", "threshold",
             "是否越過已核定的界線", _threshold_analyzer),
    Analyzer("baseline", "基線分析", "baseline",
             "扣掉負載與環境後是否偏離應有值", _baseline_analyzer),
    Analyzer("trend", "趨勢分析", "trend",
             "是否持續往界線移動，以及那是不是趨勢", _trend_analyzer),
    Analyzer("statistical", "統計分析", "statistical",
             "變化相對於設備自身的正常波動是否夠大", _statistical_analyzer),
    Analyzer("signature", "型態比對", "engineering",
             "多個量測是否符合某個已知失效模式的型態", _signature_analyzer),
    Analyzer("runtime", "運轉時數分析", "policy",
             "是否已消耗製造商建議的保養週期", _runtime_analyzer),
    Analyzer("calendar", "時程政策分析", "policy",
             "以日曆計算的定期保養是否到期", _calendar_analyzer),
    Analyzer("history", "歷史比對", "history",
             "本機或同型設備是否發生過同樣的事", _history_analyzer),
    Analyzer("engineering", "工程規則", "engineering",
             "已宣告的工程判斷規則是否成立", _engineering_analyzer),
    Analyzer("quality", "資料品質", "quality",
             "這些讀值本身可不可信", _quality_analyzer),
)

ANALYZER_KEYS = tuple(analyzer.key for analyzer in ANALYZERS)


# --------------------------------------------------------------------------
# policies
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Policy:
    """A decision policy: which analyzers are consulted, and how strictly."""

    key: str
    label: str
    description: str
    analyzers: tuple[str, ...]
    criticality_adjusted: bool = True
    threshold: float = DEFAULT_CONCERN_THRESHOLD

    def concern_threshold(self, criticality: str) -> float:
        if not self.criticality_adjusted:
            return self.threshold
        return CONCERN_THRESHOLD.get(criticality, DEFAULT_CONCERN_THRESHOLD)


POLICIES: dict[str, Policy] = {
    "threshold_only": Policy(
        key="threshold_only",
        label="僅門檻告警",
        description=(
            "傳統告警：只看量測有沒有越過界線。這是被比較的基準，"
            "不是被推薦的做法。"
        ),
        analyzers=("threshold",),
        criticality_adjusted=False,
        threshold=15.0,
    ),
    "threshold_and_policy": Policy(
        key="threshold_and_policy",
        label="門檻＋保養政策",
        description="門檻告警加上以運轉時數與日曆計算的定期保養——多數廠區的現況。",
        analyzers=("threshold", "runtime", "calendar"),
        criticality_adjusted=False,
        threshold=18.0,
    ),
    "statistical": Policy(
        key="statistical",
        label="統計與趨勢",
        description=(
            "加入基線偏離、趨勢與統計檢定：看得到還沒越線但正在移動的設備，"
            "但也還看不出儀器故障。"
        ),
        analyzers=("threshold", "baseline", "trend", "statistical", "runtime", "calendar"),
        criticality_adjusted=False,
        threshold=40.0,
    ),
    "full": Policy(
        key="full",
        label="完整分析引擎",
        description=(
            "全部十個分析器：門檻、基線、趨勢、統計、失效型態比對、"
            "保養政策、歷史比對、工程規則與資料品質。"
        ),
        analyzers=ANALYZER_KEYS,
        criticality_adjusted=False,
        threshold=DEFAULT_CONCERN_THRESHOLD,
    ),
    "full_risk_adjusted": Policy(
        key="full_risk_adjusted",
        label="完整分析＋風險分級門檻",
        description=(
            "與完整分析相同，但派工門檻依設備重要程度調整："
            "關鍵設備在較弱的證據下就去看，因為看錯的代價不對稱。"
        ),
        analyzers=ANALYZER_KEYS,
        criticality_adjusted=True,
    ),
}
DEFAULT_POLICY = "full_risk_adjusted"


# --------------------------------------------------------------------------
# the assessment
# --------------------------------------------------------------------------
@dataclass
class Assessment:
    """What the engine concluded about one asset on one date."""

    asset: dict[str, Any]
    as_of: datetime
    policy: str
    findings: list[Finding]
    health_score: float | None
    health_status: str
    health_coverage: float
    health_components: list[dict[str, Any]]
    concern: float
    likelihood: str
    risk_level: str | None
    risk_basis: str
    maintenance_required: bool
    #  Why it is required, kept apart. A machine that has run out its lubrication
    #  interval and one whose bearing is failing both need somebody to go and
    #  look, and both are `maintenance_required` — but they are answers to
    #  different questions, and scoring a due-date against a "was it degrading"
    #  label counts a correct preventive work order as a false alarm.
    condition_required: bool
    interval_required: bool
    priority: str
    recommended_action: str
    failure_mode: str | None
    window_start: str | None
    window_end: str | None
    window_basis: str
    window_reason: str
    confidence: float
    data_quality: dict[str, Any]
    measurements: list[Measurement]
    record: dict[str, Any]

    @property
    def reasons(self) -> list[str]:
        """The evidence that actually moved the decision, strongest first."""
        ordered = sorted(self.findings, key=lambda f: -abs(f.contribution))
        return [f.statement for f in ordered[:6]]

    def decision(self) -> dict[str, Any]:
        """One flat row: what the fleet view and the API read."""
        return {
            "asset_id": self.asset.get("asset_id"),
            "asset_name": self.asset.get("asset_name"),
            "asset_type": self.asset.get("asset_type"),
            "asset_type_label": self.asset.get("asset_type_label"),
            "site_id": self.asset.get("site_id"),
            "site_name": self.asset.get("site_name"),
            "location": self.asset.get("location"),
            "criticality": self.asset.get("criticality"),
            "assessed_at": self.as_of.strftime("%Y-%m-%d"),
            "policy": self.policy,
            "maintenance_required": self.maintenance_required,
            "required_because": (
                "condition" if self.condition_required
                else "policy" if self.interval_required
                else "none"
            ),
            "priority": self.priority,
            "health_score": self.health_score,
            "health_status": self.health_status,
            "health_coverage": round(self.health_coverage, 3),
            "risk_level": self.risk_level,
            "likelihood": self.likelihood,
            "consequence": self.asset.get("criticality"),
            "concern_score": round(self.concern, 2),
            "confidence": round(self.confidence, 3),
            "recommended_action": self.recommended_action,
            "suspected_failure_mode": self.failure_mode,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "window_basis": self.window_basis,
            "window_reason": self.window_reason,
            "evidence_count": len(self.findings),
            "worst_status": max(
                (m.status for m in self.measurements),
                key=lambda status: _STATUS_RANK.get(status, 0),
                default="normal",
            ),
            "data_quality_score": self.data_quality.get("min_score"),
            "data_quality_flag": self.data_quality.get("flag"),
            "observed_days": self.record.get("observed_days"),
            "interval_usage_pct": self.record.get("interval_usage_pct"),
            "runtime_hours": self.record.get("runtime_hours_total"),
            "reasons": " / ".join(self.reasons[:3]),
        }

    def evidence_rows(self) -> list[dict[str, Any]]:
        """One row per finding: what the 'Why?' panel and the LLM read."""
        return [
            {
                "asset_id": self.asset.get("asset_id"),
                "asset_name": self.asset.get("asset_name"),
                "assessed_at": self.as_of.strftime("%Y-%m-%d"),
                "policy": self.policy,
                "risk_level": self.risk_level,
                "health_score": self.health_score,
                "recommended_action": self.recommended_action,
                **finding.to_dict(),
            }
            for finding in sorted(self.findings, key=lambda f: -abs(f.contribution))
        ]


_STATUS_RANK = {"normal": 0, "warning": 1, "critical": 2, "emergency": 3}
_PRIORITY_BY_RISK = {
    "CRITICAL": "IMMEDIATE",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}


# --------------------------------------------------------------------------
# building the view
# --------------------------------------------------------------------------
def _rows_by(table: Table | None, key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if table is None or table.num_rows == 0:
        return grouped
    for row in table.to_rows():
        grouped.setdefault(str(row.get(key)), []).append(row)
    return grouped


class Engine:
    """Holds the record once, and assesses any asset on any date from it."""

    def __init__(
        self,
        *,
        features: Table,
        assets: Table,
        policies: Table | None = None,
        rules: Table | None = None,
        maintenance: Table | None = None,
        failures: Table | None = None,
        quality: Table | None = None,
    ) -> None:
        self.assets = assets.to_rows() if assets is not None else []
        self.by_asset = _rows_by(features, F.C_ASSET)
        #  Ordered once, here, so an assessment at any date is a slice rather
        #  than a sort.
        for rows in self.by_asset.values():
            rows.sort(key=lambda row: str(row.get(F.C_DAY)))
        self.policies = _rows_by(policies, "asset_type")
        self.maintenance = _rows_by(maintenance, "asset_id")
        self.failures = _rows_by(failures, "asset_id")
        self.rules = rules.to_rows() if rules is not None and rules.num_rows else []
        self.quality: dict[tuple[str, str], dict[str, Any]] = {}
        if quality is not None and quality.num_rows:
            for row in quality.to_rows():
                self.quality[(str(row.get("asset_id")), str(row.get("parameter")))] = row

        #  How often each failure mode has been seen on each class of asset.
        #  Computed once: it is the historical comparison every asset of that
        #  class will ask for.
        self.class_failures: dict[str, dict[str, int]] = {}
        asset_types = {
            str(a.get("asset_id")): str(a.get("asset_type")) for a in self.assets
        }
        for asset_id, events in self.failures.items():
            asset_type = asset_types.get(asset_id, "")
            bucket = self.class_failures.setdefault(asset_type, {})
            for event in events:
                mode = str(event.get("failure_type") or "")
                if mode:
                    bucket[mode] = bucket.get(mode, 0) + 1

        self.latest_day = max(
            (str(row.get(F.C_DAY)) for rows in self.by_asset.values() for row in rows),
            default="",
        )

    # -- one asset -------------------------------------------------------
    def view(self, asset: dict[str, Any], as_of: datetime) -> AssetView | None:
        asset_id = str(asset.get("asset_id"))
        rows = self.by_asset.get(asset_id, [])
        cutoff = as_of.strftime("%Y-%m-%d")
        usable = [row for row in rows if str(row.get(F.C_DAY)) <= cutoff]
        if not usable:
            return None

        history: dict[str, list[dict[str, Any]]] = {}
        for row in usable:
            history.setdefault(str(row.get(F.C_PARAMETER)), []).append(row)

        measurements: list[Measurement] = []
        for parameter, series in history.items():
            latest = series[-1]
            quality = self.quality.get((asset_id, parameter), {})
            measurements.append(
                Measurement(
                    parameter=parameter,
                    label=str(latest.get("parameter_label") or parameter),
                    unit=str(latest.get(F.C_UNIT) or ""),
                    day=str(latest.get(F.C_DAY)),
                    value=as_number(latest.get(F.C_MEAN)),
                    expected=as_number(latest.get(F.C_EXPECTED)),
                    deviation_pct=as_number(latest.get(F.C_DEVIATION)),
                    limit_progress_pct=as_number(latest.get(F.C_LIMIT_PROGRESS)),
                    progress_slope=as_number(latest.get(F.C_PROGRESS_SLOPE)),
                    progress_fit=as_number(latest.get(F.C_PROGRESS_FIT)),
                    status=str(latest.get(F.C_STATUS) or "normal"),
                    rank=int(as_number(latest.get(F.C_RANK)) or 0),
                    residual=as_number(latest.get(F.C_RESIDUAL)),
                    residual_spread=as_number(latest.get(F.C_RESIDUAL_SPREAD)),
                    baseline_spread=as_number(latest.get(F.C_BASELINE_SPREAD)),
                    residual_z=as_number(latest.get(F.C_RESIDUAL_Z)),
                    warning=as_number(latest.get("warning_value")),
                    critical=as_number(latest.get("critical_value")),
                    emergency=as_number(latest.get("emergency_value")),
                    direction=str(latest.get("direction") or "high"),
                    samples=int(as_number(latest.get(F.C_SAMPLES)) or 0),
                    quality_score=as_number(quality.get("quality_score")),
                    quality_issues=str(quality.get("issues") or ""),
                )
            )
        measurements.sort(key=lambda item: item.parameter)

        days = sorted({str(row.get(F.C_DAY)) for row in usable})
        runtime = max(
            (as_number(row.get(F.C_RUNTIME)) or 0.0 for row in usable), default=None
        )
        week_ago = days[max(0, len(days) - 8)]
        recent = [row for row in usable if str(row.get(F.C_DAY)) > week_ago]
        one_parameter = measurements[0].parameter if measurements else ""
        runtime_7d = sum(
            as_number(row.get(F.C_SAMPLES)) or 0.0
            for row in recent
            if str(row.get(F.C_PARAMETER)) == one_parameter
        )

        corrective = [
            as_datetime(event.get("maintenance_date"))
            for event in self.maintenance.get(asset_id, [])
            if str(event.get("maintenance_type")) == "corrective"
            and _before(event.get("maintenance_date"), as_of)
        ]
        repaired = [moment for moment in corrective if moment is not None]
        since_repair = (as_of - max(repaired)).days if repaired else None

        return AssetView(
            asset=asset,
            as_of=as_of,
            measurements=measurements,
            history=history,
            policies=self.policies.get(str(asset.get("asset_type")), []),
            maintenance=[
                event
                for event in self.maintenance.get(asset_id, [])
                if _before(event.get("maintenance_date"), as_of)
            ],
            failures=[
                event
                for event in self.failures.get(asset_id, [])
                if _before(event.get("failure_date"), as_of)
            ],
            rules=self.rules,
            class_failures=self.class_failures.get(str(asset.get("asset_type")), {}),
            observed_days=len(days),
            runtime_hours=runtime,
            runtime_7d=runtime_7d,
            days_since_repair=since_repair,
        )

    def assess(
        self,
        asset: dict[str, Any],
        *,
        as_of: datetime | None = None,
        policy: str = DEFAULT_POLICY,
    ) -> Assessment | None:
        chosen = POLICIES.get(policy) or POLICIES[DEFAULT_POLICY]
        moment = as_of or _parse_day(self.latest_day)
        view = self.view(asset, moment)
        if view is None:
            return None

        view.record = _feature_record(view)
        #  The signature analyzer's conclusions are what the history analyzer
        #  looks up and what the isolation rule reads, so it runs first and
        #  leaves them on the record. Ordering by data dependency rather than
        #  by list position keeps all three ignorant of each other.
        signature = _signature_analyzer(view)
        view.record["_signature_modes"] = [f.failure_mode for f in signature]
        view.record["signature_match_pct"] = round(
            100.0 * max((f.detail.get("match", 0.0) for f in signature), default=0.0), 2
        )

        findings: list[Finding] = []
        for analyzer in ANALYZERS:
            if analyzer.key not in chosen.analyzers:
                continue
            findings.extend(
                signature if analyzer.key == "signature" else analyzer.run(view)
            )

        return _conclude(view, findings, chosen)

    def assess_fleet(
        self, *, as_of: datetime | None = None, policy: str = DEFAULT_POLICY
    ) -> list[Assessment]:
        moment = as_of or _parse_day(self.latest_day)
        out: list[Assessment] = []
        for asset in self.assets:
            assessment = self.assess(asset, as_of=moment, policy=policy)
            if assessment is not None:
                out.append(assessment)
        return out


# --------------------------------------------------------------------------
# the feature record the scorecard and the engineering rules read
# --------------------------------------------------------------------------
def _feature_record(view: AssetView) -> dict[str, Any]:
    """One flat record per asset. The rules and the scorecard both read it.

    Flat and named, because an engineering rule is written by a person who
    knows the plant and not the code, and `vibration_rms_deviation_pct` is a
    name they can write without being shown a schema.
    """
    record: dict[str, Any] = {
        "asset_id": view.asset_id,
        "asset_type": view.asset_type,
        "criticality": view.criticality,
        "observed_days": view.observed_days,
        "measurement_count": len(view.measurements),
        "runtime_hours_total": view.runtime_hours,
        "runtime_hours_7d": view.runtime_7d,
        #  Filled in by `assess` once the signature analyzer has run, and
        #  present here so that a rule naming it always has something to read.
        #  A rule that raises on a missing name is a rule that silently never
        #  fires, which is the worst of the three possible outcomes.
        "signature_match_pct": 0.0,
        "_signature_modes": [],
        "days_since_repair": view.days_since_repair,
        "trend_window_is_clean": view.trend_window_is_clean,
    }
    for item in view.measurements:
        record[f"{item.parameter}_deviation_pct"] = item.deviation_pct
        record[f"{item.parameter}_limit_progress_pct"] = item.limit_progress_pct
        record[f"{item.parameter}_rank"] = item.rank

    ranks = [item.rank for item in view.measurements]
    progress = [
        item.limit_progress_pct for item in view.measurements
        if item.limit_progress_pct is not None
    ]
    deviations = [
        item.deviation_pct for item in view.measurements if item.deviation_pct is not None
    ]
    slopes = [
        item.progress_slope for item in view.measurements
        if item.progress_slope is not None
    ]
    record["max_threshold_rank"] = max(ranks) if ranks else 0
    record["worst_limit_progress_pct"] = max(progress) if progress else None
    record["worst_deviation_pct"] = max(deviations) if deviations else None
    record["worst_trend_slope_per_day"] = max(slopes) if slopes else None
    #  What the *second* worst measurement is doing. Real degradation moves
    #  more than one signal; an instrument that has drifted moves exactly one.
    #  Compared on limit progress, so "worst" and "second" mean the same thing
    #  for a bearing temperature and a shaft speed.
    ordered_progress = sorted(progress, reverse=True)
    record["second_limit_progress_pct"] = (
        ordered_progress[1] if len(ordered_progress) > 1 else None
    )
    record["deviating_measurements"] = sum(
        1 for value in progress if value >= SIGNIFICANT_PROGRESS_PCT
    )
    worst_trend = max(
        (item for item in view.measurements if item.progress_slope is not None),
        key=lambda item: item.progress_slope,
        default=None,
    )
    record["worst_trend_fit"] = worst_trend.progress_fit if worst_trend else None
    record["worst_trend_parameter"] = worst_trend.parameter if worst_trend else None

    ratios = [
        item.residual_spread / item.baseline_spread
        for item in view.measurements
        if item.residual_spread and item.baseline_spread and item.baseline_spread > 1e-9
    ]
    record["variability_ratio"] = max(ratios) if ratios else None

    #  Temperature headroom, for the thermal-stress rule. Named by unit rather
    #  than by parameter so it works for a winding, a bearing and an oil.
    temperatures = [
        item.limit_progress_pct
        for item in view.measurements
        if item.unit == "°C" and item.limit_progress_pct is not None
    ]
    record["temperature_headroom_pct"] = (
        round(100.0 - max(temperatures), 2) if temperatures else None
    )

    loads = [
        as_number(row.get(F.C_LOAD))
        for rows in view.history.values()
        for row in rows[-1:]
    ]
    loads = [value for value in loads if value is not None]
    record["load_pct_mean"] = round(sum(loads) / len(loads), 2) if loads else None
    ambient = [
        as_number(rows[-1].get(F.C_AMBIENT)) for rows in view.history.values() if rows
    ]
    ambient = [value for value in ambient if value is not None]
    record["ambient_temperature_c"] = (
        round(sum(ambient) / len(ambient), 2) if ambient else None
    )

    usage = []
    unknown_intervals = 0
    for policy in view.policies:
        state = _interval_state(view, policy)
        if state is None:
            continue
        if state["known"]:
            usage.append(state["usage_pct"])
        else:
            unknown_intervals += 1
    record["interval_usage_pct"] = round(max(usage), 2) if usage else None
    record["unrecorded_intervals"] = unknown_intervals

    scores = [
        item.quality_score for item in view.measurements if item.quality_score is not None
    ]
    record["min_quality_score"] = min(scores) if scores else None
    record["mean_quality_score"] = (
        round(sum(scores) / len(scores), 2) if scores else None
    )

    corrective = [
        as_datetime(event.get("maintenance_date"))
        for event in view.maintenance
        if str(event.get("maintenance_type")) == "corrective"
    ]
    corrective = [moment for moment in corrective if moment is not None]
    record["corrective_count"] = len(corrective)
    if corrective:
        days = (view.as_of - max(corrective)).days
        record["days_since_corrective"] = days
        #  Recent trouble is evidence; trouble two years ago is history. The
        #  asset's own MTBF sets the scale rather than a number chosen here.
        klass = CLASSES.get(view.asset_type)
        mtbf_days = (klass.mtbf_hours / 24.0) if klass else 1000.0
        record["recent_failure_score"] = round(
            max(0.0, 100.0 * (1.0 - days / max(30.0, mtbf_days * 0.05))), 2
        )
    else:
        record["days_since_corrective"] = None
        record["recent_failure_score"] = 0.0
    return record


# --------------------------------------------------------------------------
# concluding
# --------------------------------------------------------------------------
def _conclude(view: AssetView, findings: list[Finding], policy: Policy) -> Assessment:
    """Health, risk, window and decision, from the findings and the record."""
    record = view.record
    concern = max(0.0, min(100.0, sum(f.contribution for f in findings)))

    card = scorecard_from_config(HEALTH_SCORECARD)
    health = card.score(record)

    matrix = matrix_from_config(RISK_MATRIX)
    risk = matrix.assess({"likelihood_score": concern, "criticality": view.criticality})

    window = _window(view, findings)
    quality = {
        "min_score": record.get("min_quality_score"),
        "mean_score": record.get("mean_quality_score"),
        "flag": _quality_flag(record.get("min_quality_score")),
        "observed_days": view.observed_days,
        "measurements": len(view.measurements),
        "suspect": [
            item.parameter
            for item in view.measurements
            if item.quality_score is not None and item.quality_score < 70
        ],
    }

    threshold = policy.concern_threshold(view.criticality)
    #  Three independent reasons to act, and only one of them is the score:
    #  a measurement past the critical line and an interval that has run out
    #  are decisions in their own right.
    over_threshold = concern >= threshold
    past_critical = _forces_action(view, record)
    interval_spent = (record.get("interval_usage_pct") or 0) >= 100
    condition_required = bool(over_threshold or past_critical)
    required = bool(condition_required or interval_spent)

    #  Confidence is the evidence's own confidence, tempered by how much of the
    #  health score could be computed and by the worst instrument in the set.
    weights = sum(abs(f.weight) for f in findings) or 1.0
    evidence_confidence = (
        sum(abs(f.weight) * f.confidence for f in findings) / weights
        if findings
        else 0.4
    )
    confidence = round(
        max(
            0.1,
            min(
                0.97,
                evidence_confidence * (0.55 + 0.45 * health["coverage"])
                * _quality_factor(record.get("min_quality_score")),
            ),
        ),
        3,
    )

    action, mode = _recommendation(findings, required)
    return Assessment(
        asset=view.asset,
        as_of=view.as_of,
        policy=policy.key,
        findings=findings,
        health_score=health["score"],
        health_status=str(health["band"] or _health_band(health["score"])),
        health_coverage=health["coverage"],
        health_components=health["components"],
        concern=concern,
        likelihood=str(risk["likelihood"]),
        risk_level=risk["risk_level"],
        risk_basis=str(risk["explanation"]),
        maintenance_required=required,
        condition_required=condition_required,
        interval_required=bool(interval_spent),
        priority=_priority(risk["risk_level"], required, past_critical),
        recommended_action=action,
        failure_mode=mode,
        window_start=window["start"],
        window_end=window["end"],
        window_basis=window["basis"],
        window_reason=window["reason"],
        confidence=confidence,
        data_quality=quality,
        measurements=view.measurements,
        record=record,
    )


def _window(view: AssetView, findings: list[Finding]) -> dict[str, Any]:
    """When the work should happen, and how firm that answer is.

    Two independent estimates, and the earlier one wins: the measurement that
    is moving fastest towards its limit, and the maintenance interval that is
    closest to running out. Reported as a window rather than a date, with the
    basis that produced it — because a date carries an authority that a fit
    through three weeks of daily means does not earn.
    """
    best: dict[str, Any] | None = None

    for item in view.measurements:
        series = view.history.get(item.parameter, [])
        days: list[float] = []
        values: list[float] = []
        stamps: list[datetime] = []
        origin: datetime | None = None
        for row in series[-F.LONG_WINDOW:]:
            moment = as_datetime(row.get(F.C_DAY))
            value = as_number(row.get(F.C_LIMIT_PROGRESS))
            if moment is None or value is None:
                continue
            origin = origin or moment
            days.append((moment - origin).total_seconds() / 86400.0)
            values.append(value)
            stamps.append(moment)
        if len(days) < 4:
            continue
        fit = fit_series(days, values, stamps)
        projection = Projection(
            limits=(Limit("warning", 33.4), Limit("emergency", 100.0)),
            direction="rising",
            min_points=6,
            min_r_squared=0.3,
            horizon=180.0,
        )
        answer = projection.project(fit)
        soonest = answer.get("soonest")
        if soonest is None:
            continue
        if best is None or (soonest["periods"] or 1e9) < (best["periods"] or 1e9):
            best = {**soonest, "parameter": item.parameter, "label": item.label}

    policy_days: float | None = None
    policy_task = None
    for finding in findings:
        if finding.analyzer != "runtime":
            continue
        used = finding.detail.get("usage_pct")
        interval = finding.detail.get("interval_hours")
        if not interval or used is None:
            continue
        remaining_hours = max(0.0, interval * (100 - used) / 100.0)
        daily = max(1.0, view.runtime_7d / 7.0)
        days = remaining_hours / daily
        if policy_days is None or days < policy_days:
            policy_days = days
            policy_task = finding.detail.get("task")

    from_trend = best["periods"] if best else None
    if policy_days is not None and (from_trend is None or policy_days < from_trend):
        start = view.as_of + timedelta(days=max(0.0, policy_days * 0.75))
        end = view.as_of + timedelta(days=policy_days)
        return {
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "basis": "calculated",
            "reason": (
                f"依保養政策計算：「{policy_task}」的剩餘運轉時數換算約 "
                f"{policy_days:.0f} 天"
            ),
        }
    if best is None:
        return {
            "start": None,
            "end": None,
            "basis": "unknown",
            "reason": "沒有任何量測正往界線移動，也沒有即將到期的保養週期",
        }
    if best["basis"] == "calculated":
        return {
            "start": view.as_of.strftime("%Y-%m-%d"),
            "end": view.as_of.strftime("%Y-%m-%d"),
            "basis": "calculated",
            "reason": f"{best['label']}已越過界線：{best['reason']}",
        }
    if best["basis"] == "inferred":
        return {
            "start": None,
            "end": None,
            "basis": "inferred",
            "reason": (
                f"{best['label']}正往界線移動，但趨勢不夠穩定，無法給出日期："
                f"{best['reason']}"
            ),
        }
    earliest = best.get("date_earliest") or best.get("date")
    latest = best.get("date_latest") or best.get("date")
    return {
        "start": earliest,
        "end": latest,
        "basis": best["basis"],
        "reason": f"依{best['label']}的趨勢外推：{best['reason']}",
    }


def _forces_action(view: AssetView, record: dict[str, Any]) -> bool:
    """Whether a threshold crossing is reason enough on its own.

    A crossing normally is. Two things stop it being so, and both come from
    the same failure this whole layer exists to prevent — a work order raised
    against a healthy machine because an instrument was wrong.

    * The instrument that crossed is not trusted.
    * Nothing corroborates the crossing. Degradation moves more than one
      signal: a bearing raises vibration *and* temperature, a blocked filter
      raises pressure *and* current. A single measurement walking away from
      its expected value while everything attached to the same shaft stays
      exactly where it was is the signature of a transmitter, not of a
      machine.

    An emergency-level crossing overrides both. At that point the cost of
    being wrong about the instrument is smaller than the cost of being wrong
    about the machine, and somebody should go and look either way.
    """
    corroborated = (
        (record.get("deviating_measurements") or 0) >= 2
        or (record.get("signature_match_pct") or 0) >= 45
        or len(view.measurements) < 3
    )
    for item in view.measurements:
        trusted = item.quality_score is None or item.quality_score >= 60
        if item.rank >= 3 and trusted:
            return True
        if item.rank >= 2 and trusted and corroborated:
            return True
    return False


def _recommendation(findings: list[Finding], required: bool) -> tuple[str, str | None]:
    """What to do, taken from the strongest finding that suggests something."""
    ranked = sorted(
        (f for f in findings if f.action and f.contribution > 0),
        key=lambda f: -f.contribution,
    )
    mode = next((f.failure_mode for f in ranked if f.failure_mode), None)
    if ranked:
        return ranked[0].action or "安排檢查", mode
    if required:
        return "安排檢查以確認狀態", mode
    doubtful = [f for f in findings if f.analyzer == "quality"]
    if doubtful:
        return "先確認感測器，再判定設備狀態", mode
    return "維持既有保養排程", mode


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _priority(risk: str | None, required: bool, past_critical: bool) -> str:
    if not required:
        return "NONE"
    if past_critical and risk in ("HIGH", "CRITICAL"):
        return "IMMEDIATE"
    return _PRIORITY_BY_RISK.get(str(risk), "MEDIUM")


def _health_band(score: float | None) -> str:
    if score is None:
        return "UNKNOWN"
    for boundary, label in HEALTH_BANDS:
        if score >= boundary:
            return label
    return HEALTH_WORST


def _quality_factor(score: float | None) -> float:
    """How much a reading's own quality is allowed to scale a conclusion."""
    if score is None:
        return 0.85
    return max(0.3, min(1.0, score / 100.0))


def _quality_flag(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 90:
        return "good"
    if score >= 70:
        return "acceptable"
    if score >= 40:
        return "suspect"
    return "bad"


def _status_zh(status: str) -> str:
    return {
        "warning": "警戒", "critical": "嚴重", "emergency": "緊急", "normal": "正常",
    }.get(status, status)


def _mode_label(view: AssetView, mode: str) -> str:
    klass = CLASSES.get(view.asset_type)
    found = klass.mode(mode) if klass else None
    return found.label if found else mode


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _before(raw: Any, moment: datetime) -> bool:
    parsed = as_datetime(raw)
    return parsed is not None and parsed <= moment


def _parse_day(day: str) -> datetime:
    return as_datetime(day) or datetime.now()


def _commissioned(view: AssetView) -> datetime:
    return as_datetime(view.asset.get("commission_date")) or (
        view.as_of - timedelta(days=365)
    )


def _last_task(view: AssetView, policy: dict[str, Any]) -> datetime | None:
    """When this policy's task was last carried out, as the record says."""
    task = str(policy.get("task") or "")
    kind = str(policy.get("action_kind") or "")
    best: datetime | None = None
    for event in view.maintenance:
        matches = str(event.get("task") or "") == task or (
            kind and str(event.get("action_kind") or "") == kind
        )
        #  A corrective repair resets a usage interval whether or not it was
        #  the scheduled task: the bearing that was replaced is a new bearing.
        if not matches and str(event.get("maintenance_type")) != "corrective":
            continue
        moment = as_datetime(event.get("maintenance_date"))
        if moment is not None and (best is None or moment > best):
            best = moment
    return best


def _interval_state(view: AssetView, policy: dict[str, Any]) -> dict[str, Any] | None:
    """How much of a usage-based interval has been consumed, if that is knowable.

    `known` is the field that matters. Operating hours are not recorded against
    a maintenance event, so the hours since it are reconstructed from the rate
    the asset has actually been running at — which works only if the record
    says when the task was last done. When it does not, the honest answer is
    that this cannot be computed, and every caller here treats it that way.
    """
    interval = as_number(policy.get("interval_hours"))
    if not interval or interval <= 0:
        return None
    last = _last_task(view, policy)
    if last is None:
        return {
            "known": False,
            "interval_hours": interval,
            "hours_since": None,
            "usage_pct": None,
            "last_done": None,
        }
    elapsed_days = max(0.0, (view.as_of - last).total_seconds() / 86400.0)
    daily = view.runtime_7d / 7.0 if view.runtime_7d else 8.0
    hours = min(view.runtime_hours or 0.0, elapsed_days * max(1.0, daily))
    return {
        "known": True,
        "interval_hours": interval,
        "hours_since": hours,
        "usage_pct": 100.0 * hours / interval,
        "last_done": last.strftime("%Y-%m-%d"),
    }


def concern_of(findings: list[Finding]) -> float:
    return max(0.0, min(100.0, sum(f.contribution for f in findings)))


def analyzer_catalogue() -> list[dict[str, str]]:
    """What the engine can look at, for the UI and the API."""
    return [
        {
            "key": analyzer.key,
            "label": analyzer.label,
            "category": analyzer.category,
            "description": analyzer.description,
        }
        for analyzer in ANALYZERS
    ]


def policy_catalogue() -> list[dict[str, Any]]:
    return [
        {
            "key": policy.key,
            "label": policy.label,
            "description": policy.description,
            "analyzers": list(policy.analyzers),
            "criticality_adjusted": policy.criticality_adjusted,
        }
        for policy in POLICIES.values()
    ]
