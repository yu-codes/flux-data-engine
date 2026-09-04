"""The three providers this application contributes.

They share one engine and differ only in what they project out of it:

    asset-condition-decision   one row per asset — the decision
    asset-condition-evidence   one row per finding — the reason
    asset-maintenance-backtest replay at historical dates — the score

Two providers rather than one for the first two, because they answer different
questions and a table has one shape. A fleet list wants forty rows; the "why"
panel behind one of them wants twelve. Returning both from a single provider
would mean either a nested payload no chart can read, or a decision table with
a prose column repeated forty times.

The backtest exists because this fleet is simulated and therefore has a known
answer. That is the one thing a real fleet cannot give you, and it is what
makes "does the engineering layer actually beat threshold alarms" a measured
claim rather than an assertion — see `POLICIES` in `engine.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
    RequiredDataset,
)
from app.plugins.python_function.columnar import as_datetime
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

from . import features as F
from .engine import DEFAULT_POLICY, POLICIES, Engine, analyzer_catalogue

DECISION_KEY = "asset-condition-decision"
EVIDENCE_KEY = "asset-condition-evidence"
BACKTEST_KEY = "asset-maintenance-backtest"

#  The reference tables every assessment needs. Declared rather than opened
#  from disk, so they are versioned, traceable and replaceable — the plugin
#  never touches a file or a repository.
_REFERENCES = (
    RequiredDataset("assets", F.ASSETS_DATASET, "設備主檔：類型、重要程度、啟用日期"),
    RequiredDataset("policies", F.POLICY_DATASET, "保養政策：時數與日曆週期"),
    RequiredDataset("rules", F.RULES_DATASET, "工程判斷規則", required=False),
    RequiredDataset("maintenance", F.MAINTENANCE_DATASET, "維修歷史", required=False),
    RequiredDataset("failures", F.FAILURE_DATASET, "故障歷史", required=False),
    RequiredDataset("quality", F.QUALITY_DATASET, "各量測序列的資料品質", required=False),
)

#  The backtest needs one thing the other two do not: what actually happened.
_BACKTEST_REFERENCES = (
    *_REFERENCES,
    RequiredDataset(
        "truth",
        F.TRUTH_DATASET,
        "模擬機隊的實際狀態：劣化起點、故障時點與修復時點",
        required=False,
    ),
)

_POLICY_FIELD = FieldSpec(
    "policy",
    FieldType.STRING,
    required=False,
    default=DEFAULT_POLICY,
    enum=tuple(POLICIES),
    description="which analyzers are consulted, and how strictly",
)
_AS_OF_FIELD = FieldSpec(
    "as_of",
    FieldType.STRING,
    required=False,
    description=(
        "assess the fleet as it stood on this date (YYYY-MM-DD); "
        "the latest day in the data when empty"
    ),
)


def _truth(context: ExecutionContext) -> dict[str, list[dict[str, Any]]]:
    """What was actually wrong with each asset, and when.

    One row per degradation episode, grouped by asset. Read from the
    simulator's own record when it is there. This is the only place in the
    application that touches it, and it is used only to score a policy — never
    to make a decision.
    """
    table = (context.datasets or {}).get("truth")
    if table is None or table.num_rows == 0:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in table.to_rows():
        grouped.setdefault(str(row.get("asset_id")), []).append(row)
    return grouped


def _engine(context: ExecutionContext) -> Engine:
    """Build the engine from the step's input and the declared references."""
    if not context.input.has_table:
        raise ValidationError(
            f"this model reads the '{F.DAILY_FEATURES}' dataset as its input"
        )
    datasets = context.datasets or {}
    assets = datasets.get("assets")
    if assets is None:
        raise ValidationError(f"the '{F.ASSETS_DATASET}' dataset is required")
    return Engine(
        features=context.input.table,
        assets=assets,
        policies=datasets.get("policies"),
        rules=datasets.get("rules"),
        maintenance=datasets.get("maintenance"),
        failures=datasets.get("failures"),
        quality=datasets.get("quality"),
    )


def _as_of(config: dict[str, Any], engine: Engine) -> datetime:
    raw = config.get("as_of")
    parsed = as_datetime(raw) if raw else None
    return parsed or (as_datetime(engine.latest_day) or datetime.now())


def _check_features(schema_fields) -> ValidationResult:
    """Whether this dataset is actually the feature table."""
    result = ValidationResult()
    available = {f.name for f in schema_fields}
    missing = [c for c in F.REQUIRED_FEATURE_COLUMNS if c not in available]
    if missing:
        result.add_error(
            f"this dataset is missing {len(missing)} column(s) the engine reads, "
            f"starting with {missing[:4]}; it should be '{F.DAILY_FEATURES}'"
        )
    return result


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------
class AssetDecisionPlugin:
    """The fleet, assessed: one row per asset."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=DECISION_KEY,
            name="設備維護決策",
            model_type=ModelType.RULE,
            runtime=RuntimeKind.PYTHON,
            version="1",
            timeout_seconds=600,
            description=(
                "十個分析器的結論合成為一份維護決策：健康分數、風險等級、"
                "建議措施、維修窗口與信心度。不使用機器學習——每一項結論都"
                "指得出是哪一條證據支持的。"
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.PREDICTION),
            required_datasets=_REFERENCES,
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    _POLICY_FIELD,
                    _AS_OF_FIELD,
                    FieldSpec(
                        "asset_id",
                        FieldType.STRING,
                        required=False,
                        description="assess one asset rather than the whole fleet",
                    ),
                    FieldSpec(
                        "required_only",
                        FieldType.BOOLEAN,
                        required=False,
                        default=False,
                        description="return only the assets that need attention",
                    ),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description=f"the '{F.DAILY_FEATURES}' table",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per asset: decision, health, risk and window",
            ),
            examples=[
                {
                    "name": "整廠評估（風險分級門檻）",
                    "configuration": {"policy": DEFAULT_POLICY},
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        policy = str(config.get("policy") or DEFAULT_POLICY)
        if policy not in POLICIES:
            result.add_error(
                f"unknown policy '{policy}'",
            )
        raw = config.get("as_of")
        if raw and as_datetime(raw) is None:
            result.add_error(f"as_of '{raw}' is not a readable date")
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        return _check_features(schema_fields)

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        engine = _engine(context)
        moment = _as_of(config, engine)
        policy = str(config.get("policy") or DEFAULT_POLICY)
        wanted = config.get("asset_id")

        assets = [
            asset for asset in engine.assets
            if not wanted or str(asset.get("asset_id")) == str(wanted)
        ]
        if not assets:
            raise ValidationError(f"no asset '{wanted}' in the asset register")

        rows: list[dict[str, Any]] = []
        risks: dict[str, int] = {}
        for asset in assets:
            assessment = engine.assess(asset, as_of=moment, policy=policy)
            if assessment is None:
                #  No readings up to this date. Said plainly rather than
                #  dropped, because an asset missing from a fleet view reads
                #  as an asset that is fine.
                rows.append(
                    {
                        "asset_id": asset.get("asset_id"),
                        "asset_name": asset.get("asset_name"),
                        "asset_type": asset.get("asset_type"),
                        "site_id": asset.get("site_id"),
                        "criticality": asset.get("criticality"),
                        "assessed_at": moment.strftime("%Y-%m-%d"),
                        "policy": policy,
                        "maintenance_required": False,
                        "priority": "NONE",
                        "health_score": None,
                        "health_status": "UNKNOWN",
                        "risk_level": None,
                        "confidence": 0.0,
                        "recommended_action": "尚無此日期之前的量測資料，無法評估",
                        "window_basis": "unknown",
                        "reasons": "沒有可用的量測資料",
                    }
                )
                continue
            decision = assessment.decision()
            level = str(decision["risk_level"])
            risks[level] = risks.get(level, 0) + 1
            rows.append(decision)

        if config.get("required_only"):
            rows = [row for row in rows if row.get("maintenance_required")]

        scored = [
            row["health_score"] for row in rows
            if row.get("health_score") is not None
        ]
        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                Table.from_rows(rows),
                kind=ResultKind.TABLE,
                summary={
                    "policy": policy,
                    "as_of": moment.strftime("%Y-%m-%d"),
                    "risk_levels": risks,
                    "analyzers": [a["key"] for a in analyzer_catalogue()
                                  if a["key"] in POLICIES[policy].analyzers],
                },
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "assets": len(rows),
                "maintenance_required": sum(
                    1 for row in rows if row.get("maintenance_required")
                ),
                "mean_health": round(sum(scored) / len(scored), 2) if scored else None,
                "worst_health": min(scored) if scored else None,
                **{f"risk_{level}": count for level, count in risks.items()},
            },
            logs=context.logs,
        )


# --------------------------------------------------------------------------
# the evidence
# --------------------------------------------------------------------------
class AssetEvidencePlugin:
    """Why: one row per finding, strongest first."""

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=EVIDENCE_KEY,
            name="設備維護判斷依據",
            model_type=ModelType.RULE,
            runtime=RuntimeKind.PYTHON,
            version="1",
            timeout_seconds=600,
            description=(
                "同一份分析，但輸出的是每一條證據：哪個分析器、依據什麼數字、"
                "對結論的貢獻有多少。這是「Why?」面板與 LLM 推理讀的表。"
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.CALCULATION, ExecutionKind.EVALUATION),
            required_datasets=_REFERENCES,
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    _POLICY_FIELD,
                    _AS_OF_FIELD,
                    FieldSpec("asset_id", FieldType.STRING, required=False,
                              description="one asset rather than the whole fleet"),
                    FieldSpec("required_only", FieldType.BOOLEAN, required=False,
                              default=False,
                              description="only the assets that need attention"),
                    FieldSpec("min_contribution", FieldType.FLOAT, required=False,
                              default=0.0,
                              description="drop findings weaker than this, in "
                                          "absolute contribution"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description=f"the '{F.DAILY_FEATURES}' table",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per finding, with its analyzer and contribution",
            ),
            examples=[
                {
                    "name": "需要處置的設備，逐條依據",
                    "configuration": {"policy": DEFAULT_POLICY, "required_only": True},
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        return AssetDecisionPlugin().validate(definition)

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        return _check_features(schema_fields)

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        engine = _engine(context)
        moment = _as_of(config, engine)
        policy = str(config.get("policy") or DEFAULT_POLICY)
        wanted = config.get("asset_id")
        floor = float(config.get("min_contribution", 0.0) or 0.0)
        required_only = bool(config.get("required_only"))

        rows: list[dict[str, Any]] = []
        by_analyzer: dict[str, int] = {}
        assessed = 0
        for asset in engine.assets:
            if wanted and str(asset.get("asset_id")) != str(wanted):
                continue
            assessment = engine.assess(asset, as_of=moment, policy=policy)
            if assessment is None:
                continue
            if required_only and not assessment.maintenance_required:
                continue
            assessed += 1
            for row in assessment.evidence_rows():
                if abs(float(row.get("contribution") or 0.0)) < floor:
                    continue
                #  The nested detail is a mapping and Arrow would have to guess
                #  a struct type for it; the columns above already carry the
                #  numbers a chart or a table needs.
                row.pop("detail", None)
                by_analyzer[row["analyzer"]] = by_analyzer.get(row["analyzer"], 0) + 1
                rows.append(row)

        if not rows:
            rows = [
                {
                    "asset_id": None,
                    "assessed_at": moment.strftime("%Y-%m-%d"),
                    "policy": policy,
                    "analyzer": None,
                    "category": None,
                    "statement": "在此條件下沒有任何分析器提出證據",
                    "weight": 0.0,
                    "confidence": 0.0,
                    "contribution": 0.0,
                }
            ]

        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                Table.from_rows(rows),
                kind=ResultKind.TABLE,
                summary={
                    "policy": policy,
                    "as_of": moment.strftime("%Y-%m-%d"),
                    "assets": assessed,
                    "by_analyzer": by_analyzer,
                },
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} result",
            ),
            metrics={
                "assets": assessed,
                "findings": len(rows),
                "findings_per_asset": (
                    round(len(rows) / assessed, 2) if assessed else 0.0
                ),
                **{f"analyzer_{name}": count for name, count in by_analyzer.items()},
            },
            logs=context.logs,
        )


# --------------------------------------------------------------------------
# the backtest
# --------------------------------------------------------------------------
class AssetBacktestPlugin:
    """Replay a decision policy at historical dates and score it.

    The label is **"was this machine actually degrading when the engine
    looked"**, not "did it break within a fortnight". The distinction decides
    whether the numbers mean anything, and getting it wrong is the standard
    way this kind of evaluation is done badly.

    Scored the second way, an engine that correctly identifies a bearing six
    weeks before it fails is marked wrong on every date but the last two — and
    a machine still degrading when the record ends is marked wrong on every
    date, because its failure falls outside the data. Precision came out at
    0.16 for an engine that was, on inspection, right almost every time.
    Optimising against that number would have meant making the engine slower
    to notice things, which is the opposite of the point.

    So a positive case is an asset between the onset of a real degradation and
    its repair, taken from the simulator's own record. Where that record is not
    available — which is every real fleet — the provider falls back to the
    failure-within-horizon rule and says so in `label_basis`, because an
    approximate label named as one is honest and an unnamed one is not.

    Lead time is reported beside precision and recall either way, because a
    system that catches everything the day before it breaks has perfect recall
    and no use.
    """

    def describe(self) -> PluginDescriptor:
        return PluginDescriptor(
            key=BACKTEST_KEY,
            name="維護決策政策回測",
            model_type=ModelType.STATISTICAL,
            runtime=RuntimeKind.PYTHON,
            version="1",
            timeout_seconds=1800,
            description=(
                "在歷史上的每個評估日重跑決策政策，對照實際發生的故障，"
                "計算精確率、召回率、F1 與平均提前天數。"
            ),
            trainable=False,
            supported_kinds=(ExecutionKind.EVALUATION,),
            required_datasets=_BACKTEST_REFERENCES,
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    _POLICY_FIELD,
                    FieldSpec("horizon_days", FieldType.INTEGER, required=False,
                              default=14,
                              description="a decision counts as right if a failure "
                                          "follows within this many days"),
                    FieldSpec("step_days", FieldType.INTEGER, required=False, default=7,
                              description="how often the engine is asked to look"),
                    FieldSpec("warmup_days", FieldType.INTEGER, required=False,
                              default=35,
                              description="days at the start of the record the "
                                          "rolling windows need before any "
                                          "assessment is meaningful"),
                    FieldSpec("label", FieldType.STRING, required=False,
                              default="auto",
                              enum=("auto", "degradation", "failure_horizon"),
                              description=(
                                  "what counts as a case worth flagging: an asset "
                                  "actually degrading at that date, or one that "
                                  "failed within the horizon. 'auto' uses the "
                                  "first when a ground-truth table is available"
                              )),
                    FieldSpec("detail", FieldType.BOOLEAN, required=False,
                              default=False,
                              description="return every assessment rather than the "
                                          "summary"),
                ],
            ),
            input_contract=Contract(
                shape=ContractShape.TABLE,
                description=f"the '{F.DAILY_FEATURES}' table",
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="one row per evaluation date, plus the totals in metrics",
            ),
            examples=[
                {
                    "name": "完整分析引擎，14 天視野",
                    "configuration": {"policy": "full", "horizon_days": 14},
                }
            ],
        )

    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = AssetDecisionPlugin().validate(definition)
        config = definition.configuration or {}
        if int(config.get("horizon_days", 14) or 14) < 1:
            result.add_error("horizon_days must be at least 1")
        if int(config.get("step_days", 7) or 7) < 1:
            result.add_error("step_days must be at least 1")
        return result

    def check_dataset(self, definition: ModelDefinition, schema_fields) -> ValidationResult:
        return _check_features(schema_fields)

    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        config = {**(context.definition.configuration or {}), **context.parameters}
        engine = _engine(context)
        policy = str(config.get("policy") or DEFAULT_POLICY)
        horizon = int(config.get("horizon_days", 14) or 14)
        step = int(config.get("step_days", 7) or 7)
        warmup = int(config.get("warmup_days", 35) or 35)
        detail = bool(config.get("detail"))

        days = sorted(
            {
                str(row.get(F.C_DAY))
                for rows in engine.by_asset.values()
                for row in rows
            }
        )
        if len(days) < warmup + horizon + step:
            raise ValidationError(
                f"the record holds {len(days)} days, which is not enough for a "
                f"{warmup}-day warm-up and a {horizon}-day horizon"
            )

        #  What actually happened, taken from the record rather than from
        #  anything the engine produced.
        failures: dict[str, list[datetime]] = {}
        for asset_id, events in engine.failures.items():
            for event in events:
                moment = as_datetime(event.get("failure_date"))
                if moment is not None:
                    failures.setdefault(asset_id, []).append(moment)

        truth = _truth(context)
        wanted = str(config.get("label", "auto") or "auto")
        basis = (
            "degradation"
            if (wanted == "degradation" or (wanted == "auto" and truth))
            else "failure_horizon"
        )
        if basis == "degradation" and not truth:
            raise ValidationError(
                f"scoring against real degradation needs the "
                f"'{F.TRUTH_DATASET}' dataset, which this workspace does not have"
            )
        context.log(f"scoring against '{basis}' labels")

        first = as_datetime(days[warmup]) or as_datetime(days[0])
        last = as_datetime(days[-1])
        if first is None or last is None:
            raise ValidationError("the feature table has no readable dates")
        #  The final `horizon` days cannot be scored: nothing has had time to
        #  happen yet. Scoring them anyway is how a backtest reports a recall
        #  it has not measured.
        cutoff = last - timedelta(days=horizon)

        rows: list[dict[str, Any]] = []
        detail_rows: list[dict[str, Any]] = []
        totals = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        leads: list[float] = []
        #  Per-date recall answers "on how many days was it right"; a planner
        #  asks "did we catch it at all, and how early". Both are reported,
        #  because a policy can score badly on the first while being perfectly
        #  usable — it noticed on day forty of a sixty-day degradation.
        first_seen: dict[str, datetime] = {}
        episodes = {
            asset_id: record
            for asset_id, record in truth.items()
            if _intervals(record)
        }
        moment = first
        checkpoints = 0
        while moment <= cutoff:
            if context.should_stop():
                context.log(f"stopped after {checkpoints} evaluation dates")
                break
            checkpoints += 1
            point = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
            for asset in engine.assets:
                assessment = engine.assess(asset, as_of=moment, policy=policy)
                if assessment is None:
                    continue
                asset_id = str(asset.get("asset_id"))
                upcoming = [
                    failed for failed in failures.get(asset_id, [])
                    if moment < failed <= moment + timedelta(days=horizon)
                ]
                degrading = _degrading_at(truth.get(asset_id), moment)
                worth_flagging = degrading if basis == "degradation" else bool(upcoming)
                #  Only the condition-driven half is scored. A work order raised
                #  because a lubrication interval ran out is correct and has
                #  nothing to do with whether the machine was degrading; counting
                #  it against a degradation label measures the wrong system.
                flagged = (
                    assessment.condition_required
                    if basis == "degradation"
                    else assessment.maintenance_required
                )
                outcome = (
                    "tp" if (flagged and worth_flagging)
                    else "fp" if flagged
                    else "fn" if worth_flagging
                    else "tn"
                )
                point[outcome] += 1
                totals[outcome] += 1
                if outcome == "tp":
                    #  How far ahead of the failure this was caught. Reported
                    #  only where the record says when the failure came, which
                    #  is what makes it a lead time rather than a guess.
                    ahead = _days_ahead(truth.get(asset_id), upcoming, moment)
                    if ahead is not None:
                        leads.append(ahead)
                    first_seen.setdefault(asset_id, moment)
                if detail:
                    detail_rows.append(
                        {
                            "assessed_at": moment.strftime("%Y-%m-%d"),
                            "asset_id": asset_id,
                            "criticality": asset.get("criticality"),
                            "flagged": flagged,
                            "degrading": degrading,
                            "failed_within_horizon": bool(upcoming),
                            "outcome": outcome,
                            "health_score": assessment.health_score,
                            "risk_level": assessment.risk_level,
                            "concern": round(assessment.concern, 2),
                            "recommended_action": assessment.recommended_action,
                        }
                    )
            rows.append(
                {
                    "assessed_at": moment.strftime("%Y-%m-%d"),
                    "policy": policy,
                    "horizon_days": horizon,
                    **point,
                    "flagged": point["tp"] + point["fp"],
                    "failures_ahead": point["tp"] + point["fn"],
                    **_scores(point),
                }
            )
            moment += timedelta(days=step)

        scores = _scores(totals)
        detected = [asset_id for asset_id in episodes if asset_id in first_seen]
        detection_leads = [
            ahead
            for asset_id in detected
            if (ahead := _days_ahead(episodes[asset_id], [], first_seen[asset_id]))
            is not None
        ]
        episode_scores = {
            "episodes": len(episodes),
            "episodes_detected": len(detected),
            "episode_recall": (
                round(len(detected) / len(episodes), 4) if episodes else 0.0
            ),
            "mean_detection_lead_days": (
                round(sum(detection_leads) / len(detection_leads), 2)
                if detection_leads
                else None
            ),
        }
        summary = {
            "policy": policy,
            "label": POLICIES[policy].label,
            "label_basis": basis,
            "horizon_days": horizon,
            "evaluation_dates": checkpoints,
            **totals,
            **scores,
            **episode_scores,
            "mean_lead_days": round(sum(leads) / len(leads), 2) if leads else None,
        }
        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                Table.from_rows(detail_rows or rows),
                kind=ResultKind.TABLE,
                summary=summary,
                materialise_as_dataset=False,
            ),
            metrics={
                **{k: v for k, v in summary.items() if not isinstance(v, str)},
                "assessments": totals["tp"] + totals["fp"] + totals["fn"] + totals["tn"],
            },
            logs=context.logs,
        )


#  (onset, failure, repaired) — the shape a degradation episode has.
Episode = tuple[datetime, "datetime | None", "datetime | None"]


def _intervals(rows: list[dict[str, Any]] | None) -> list[Episode]:
    """Every degradation this asset went through, as (onset, failure, repaired).

    Rows without an onset are the assets that never degraded; they are in the
    key so their scenario is recorded, and they contribute no interval.
    """
    out = []
    for row in rows or []:
        onset = as_datetime(row.get("onset"))
        if onset is None:
            continue
        out.append(
            (onset, as_datetime(row.get("failure")), as_datetime(row.get("repaired")))
        )
    return out


def _degrading_at(rows: list[dict[str, Any]] | None, moment: datetime) -> bool:
    """Whether this asset was genuinely degrading on this date.

    Between the onset of a degradation and the repair that ended it, for any
    of its episodes. An asset whose degradation is still developing when the
    record ends counts on every date after its onset, which is the case the
    failure-horizon label got exactly backwards.
    """
    for onset, _failure, repaired in _intervals(rows):
        if moment >= onset and (repaired is None or moment < repaired):
            return True
    return False


def _days_ahead(
    rows: list[dict[str, Any]] | None, upcoming: list[datetime], moment: datetime
) -> float | None:
    """Days between this assessment and the failure it was about."""
    if upcoming:
        return float((min(upcoming) - moment).days)
    ahead = [
        (failure - moment).days
        for _onset, failure, _repaired in _intervals(rows)
        if failure is not None and failure > moment
    ]
    return float(min(ahead)) if ahead else None


def _scores(counts: dict[str, int]) -> dict[str, float]:
    """Precision, recall, F1 and specificity from a confusion count.

    Written out rather than imported so that the one place the platform scores
    a binary decision is visible beside the decision being scored.
    """
    tp, fp, fn, tn = counts["tp"], counts["fp"], counts["fn"], counts["tn"]
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    #  Reported because a maintenance fleet is mostly healthy: a policy that
    #  flags nothing scores 0.97 on accuracy and is worthless, while one that
    #  flags everything has perfect recall and empties the workshop.
    specificity = tn / (tn + fp) if (tn + fp) else 0.0
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "specificity": round(specificity, 4),
        "alert_rate": round((tp + fp) / max(1, tp + fp + fn + tn), 4),
    }
