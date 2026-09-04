"""The maintenance worked example: everything the platform can do, once, on a fleet.

    telemetry (600k readings)
        -> four Pipelines of standard transforms and providers
        -> a daily condition table with expected values, limits and trends
        -> two data-quality Models over two different streams
        -> a decision Model, an evidence Model and an LLM reasoning Model
        -> twenty Visualizations across five Dashboards
        -> an Experiment comparing five decision policies against the record
        -> Evaluations, a Schedule and two Reports
        -> a published Application with a page of its own

Everything here goes through the ordinary public services, so a reader can open
any of it and see how it was made. Every step checks for what it creates before
creating it, so seeding twice leaves one of each.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.core.container import Services
from app.modules.execution.domain.ports import RunInline
from app.shared.errors import FluxError

from .. import features as F
from ..datagen import FILES
from ..engine import POLICIES
from ..paths import data_dir

logger = logging.getLogger(__name__)

# -- names the fixture also uses -------------------------------------------
DECISION_MODEL = "設備維護決策"
EVIDENCE_MODEL = "設備維護判斷依據"
REASONING_MODEL = "維護判斷說明（LLM）"
PROJECTION_MODEL = "劣化到限期預估"
HEALTH_SCORECARD_MODEL = "設備健康評分卡"
RISK_MATRIX_MODEL = "維護風險矩陣"

DECISION_DATASET = f"{DECISION_MODEL} result"
EVIDENCE_DATASET = f"{EVIDENCE_MODEL} result"
REASONING_DATASET = f"{REASONING_MODEL} result"
PROJECTION_DATASET = f"{PROJECTION_MODEL} result"

BACKTEST_MODEL = "維護決策政策回測"
EXPERIMENT_NAME = "維護決策政策比較"
SCHEDULE_NAME = "每日機隊健康評估"
FLEET_REPORT = "機隊設備健康與維護決策"
VALIDATION_REPORT = "維護決策政策驗證"
APPLICATION = "設備預防性維護分析"

#  Small enough that a fresh install finishes seeding promptly. The UI can
#  run a denser sweep on demand.
BACKTEST_STEP_DAYS = 7
BACKTEST_HORIZON_DAYS = 14

DASHBOARDS = [
    {
        "name": "機隊健康總覽",
        "group": "fleet",
        "description": "四十台設備現在的健康、風險與處置優先順序，以及它們落在哪些廠區。",
    },
    {
        "name": "判斷依據與分析器",
        "group": "evidence",
        "description": (
            "每個分析器提出了多少證據、平均貢獻多少，"
            "以及證據類別如何隨風險變化。"
        ),
    },
    {
        "name": "狀態、門檻與趨勢",
        "group": "condition",
        "description": "各量測距離界線多遠、負載是否解釋得了偏差，以及門檻狀態的組成。",
    },
    {
        "name": "資料品質",
        "group": "quality",
        "description": "在相信任何結論之前：讀值到齊了嗎、儀器是否可信。",
    },
    {
        "name": "維護與故障歷史",
        "group": "history",
        "description": "過去做了哪些維護、花了多少、停了多久，以及哪些失效模式最常見。",
    },
]


def seed_maintenance_example(services: Services) -> None:
    """Build the whole example. Safe to run again: every step checks first."""
    telemetry = services.datasets.datasets.get_by_name(F.TELEMETRY_DATASET)
    if not telemetry:
        logger.info("maintenance telemetry absent; skipping the worked example")
        return

    #  The example must exist in full by the time the API is up, so its
    #  executions run in-process even where the deployment uses a worker.
    services.executions.dispatcher = RunInline()

    _refresh_stale_datasets(services)

    conditioned = _run_pipeline(
        services,
        name=F.CONDITIONING_PIPELINE,
        input_dataset=telemetry,
        steps=F.conditioning_steps(_reference_ids(services)),
        description=(
            "原始讀值 → 可信、且確實在運轉中的讀值。去重、剔除無效值、"
            "併入運轉狀態與環境，只留下機器真的在轉的時段。"
        ),
    )
    if not conditioned:
        return
    daily = _run_pipeline(
        services,
        name=F.DAILY_PIPELINE,
        input_dataset=conditioned,
        steps=F.daily_steps(),
        description="每台設備每個量測每天一列：平均、峰值、離散度、當日負載與環境。",
    )
    if not daily:
        return

    quality = _run_quality(services, conditioned, daily)

    features = _run_pipeline(
        services,
        name=F.FEATURES_PIPELINE,
        input_dataset=daily,
        steps=F.feature_steps(_reference_ids(services)),
        description=(
            "併入響應模型算出「應有值」，扣掉負載與環境後得到殘差；"
            "併入門檻表判定狀態；再計算 7／21／30 天的滾動統計與趨勢。"
        ),
    )
    if not features:
        return
    _run_pipeline(
        services,
        name=F.SNAPSHOT_PIPELINE,
        input_dataset=features,
        steps=F.snapshot_steps(),
        description="每個量測最新的一天：全機隊的現況，供總覽圖表使用。",
    )

    decisions = _run_model(services, DECISION_MODEL, features, kind="calculation")
    evidence = _run_model(services, EVIDENCE_MODEL, features, kind="calculation")
    _run_model(services, PROJECTION_MODEL, features, kind="calculation")
    reasoning = (
        _run_model(services, REASONING_MODEL, evidence, kind="calculation")
        if evidence
        else None
    )
    _run_model(services, HEALTH_SCORECARD_MODEL, features, kind="calculation")
    _run_model(services, RISK_MATRIX_MODEL, features, kind="calculation")

    charts = _seed_charts(
        services,
        {
            "decisions": decisions,
            "evidence": evidence,
            "features": features,
            "quality": quality.get("measurement"),
            "sampling": quality.get("sampling"),
            "maintenance": services.datasets.datasets.get_by_name(F.MAINTENANCE_DATASET),
            "failures": services.datasets.datasets.get_by_name(F.FAILURE_DATASET),
        },
    )
    backtests = _seed_experiment(services, features)
    _seed_schedule(services)
    _seed_fleet_report(services, charts, decisions, evidence, reasoning)
    _seed_validation_report(services, charts, backtests)


# --------------------------------------------------------------------------
# keeping the ingested copy honest
# --------------------------------------------------------------------------
def _refresh_stale_datasets(services: Services) -> None:
    """Re-read any dataset whose file has been rewritten since it was ingested.

    A fixture creates a dataset once and never looks at the file again, which
    is correct for a file that never changes. This one does: the fleet is
    generated, and a change to what the generator records — a new column in the
    answer key, say — leaves the platform holding a copy that predates it,
    silently, with no failure anywhere. The version an analysis reads and the
    file on disk are then two different datasets with one name.

    Compared against `meta.json`, which the generator rewrites whenever it
    regenerates. Refreshing appends an immutable version rather than editing
    the old one, so the previous analysis stays reproducible.
    """
    meta_path = data_dir() / FILES["meta"]
    if not meta_path.exists():
        return
    stamp = datetime.fromtimestamp(meta_path.stat().st_mtime, tz=UTC)

    for name in _INGESTED_DATASETS:
        dataset = services.datasets.datasets.get_by_name(name)
        if dataset is None or not dataset.current_version_id:
            continue
        try:
            version = services.datasets.get_version(dataset.current_version_id)
        except FluxError:
            continue
        created = version.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created >= stamp:
            continue
        try:
            services.datasets.refresh(dataset.id)
            logger.info("refreshed '%s': the file is newer than the dataset", name)
        except FluxError as exc:
            logger.warning("could not refresh '%s': %s", name, exc)


#  Every dataset that comes from a generated file. The pipelines' outputs are
#  not here: they are derived, and re-running the pipeline is what updates them.
_INGESTED_DATASETS = (
    F.TELEMETRY_DATASET, F.OPERATING_DATASET, F.ENVIRONMENT_DATASET,
    F.ASSETS_DATASET, F.SPECIFICATIONS_DATASET, F.SENSORS_DATASET,
    F.MAINTENANCE_DATASET, F.FAILURE_DATASET, F.POLICY_DATASET,
    F.THRESHOLD_DATASET, F.RESPONSE_DATASET, F.RULES_DATASET, F.TRUTH_DATASET,
)


# --------------------------------------------------------------------------
# pipelines
# --------------------------------------------------------------------------
def _reference_ids(services: Services) -> dict[str, str]:
    """The lookup tables the joins wire in, by name.

    A missing one is a real problem rather than something to work around: the
    pipeline that reads it cannot be built, and saying which table is absent
    is more useful than a step that silently joins against nothing.
    """
    wanted = (
        F.OPERATING_DATASET, F.ASSETS_DATASET, F.ENVIRONMENT_DATASET,
        F.RESPONSE_DATASET, F.THRESHOLD_DATASET,
    )
    found: dict[str, str] = {}
    for name in wanted:
        dataset = services.datasets.datasets.get_by_name(name)
        if dataset is None:
            raise FluxError(f"the '{name}' dataset has not been created")
        found[name] = dataset.id
    return found


def _run_pipeline(
    services: Services,
    *,
    name: str,
    input_dataset,
    steps: list[dict[str, Any]],
    description: str,
):
    """Create or upgrade the pipeline, run it once, return its output dataset."""
    wanted = [step["name"] for step in steps]
    pipeline = services.pipelines.repository.get_by_name(name)
    if pipeline and [s.name for s in pipeline.steps] != wanted:
        #  An install seeded by an earlier version carries the older chain.
        #  Bring it up to date rather than leaving two half-pipelines.
        try:
            pipeline = services.pipelines.update(pipeline.id, {"steps": steps})
            logger.info("upgraded '%s' to %s steps", name, len(steps))
        except FluxError as exc:
            logger.warning("could not upgrade the '%s' pipeline: %s", name, exc)
            return None
    if not pipeline:
        try:
            pipeline = services.pipelines.create(
                name=name,
                input_dataset_id=input_dataset.id,
                description=description,
                tags=["maintenance"],
                steps=steps,
            )
        except FluxError as exc:
            logger.warning("could not seed the '%s' pipeline: %s", name, exc)
            return None

    runs = services.pipelines.list_runs(pipeline_id=pipeline.id, limit=1)
    run = runs[0] if runs else None
    if run and [s.step_name for s in run.step_runs] != wanted:
        run = None
    if run and not any(s.dataset_id for s in run.step_runs):
        #  A run that left no dataset behind is not a run anything downstream
        #  can reuse, whatever its step names say.
        run = None
    if run and _older_than_input(services, run, input_dataset):
        #  The input has been re-read since this run. Reusing it would leave
        #  the derived table describing data the platform no longer holds —
        #  and nothing anywhere would say so, because the run itself
        #  succeeded. Refreshing the sources without re-deriving from them is
        #  how a chain comes to disagree with itself quietly.
        run = None
    if not run or run.status.value != "succeeded":
        try:
            run = services.pipelines.run(pipeline.id)
        except FluxError as exc:
            logger.warning("the '%s' pipeline could not be run: %s", name, exc)
            return None
    if run.status.value != "succeeded":
        logger.warning("'%s' finished %s: %s", name, run.status.value, run.error)
        return None

    final = next(
        (s for s in reversed(run.step_runs) if s.dataset_id), None
    )
    if final is None:
        logger.warning("'%s' produced no dataset", name)
        return None
    dataset = services.datasets.get(final.dataset_id)
    logger.info("'%s' produced %s rows", name, final.row_count)
    return dataset


def _older_than_input(services: Services, run, input_dataset) -> bool:
    """Whether this run predates the version of the data it was run on."""
    if input_dataset is None or not input_dataset.current_version_id:
        return False
    try:
        version = services.datasets.get_version(input_dataset.current_version_id)
    except FluxError:
        return False
    created = version.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    started = run.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return started < created


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
def _run_model(services: Services, name: str, dataset, *, kind: str):
    """Run a seeded model once against a dataset and return its output dataset."""
    model = services.models.repository.get_by_name(name)
    if model is None or dataset is None:
        logger.info("skipping '%s': the model or its input is missing", name)
        return None
    output_name = f"{name} result"
    existing = services.datasets.datasets.get_by_name(output_name)
    if existing and existing.current_version_id and not _output_is_stale(
        services, existing, dataset
    ):
        return existing
    try:
        execution = services.executions.submit(
            model_id=model.id, kind=kind, dataset_id=dataset.id
        )
    except FluxError as exc:
        logger.warning("could not run '%s': %s", name, exc)
        return None
    if execution.status.value != "succeeded":
        logger.warning(
            "'%s' finished %s: %s",
            name, execution.status.value, execution.error,
        )
        return None
    logger.info("'%s': %s", name, execution.metrics)
    return services.datasets.datasets.get_by_name(output_name)


def _output_is_stale(services: Services, output, source) -> bool:
    """Whether a model's stored result predates the data it was computed from."""
    try:
        produced = services.datasets.get_version(output.current_version_id)
        consumed = services.datasets.get_version(source.current_version_id)
    except (FluxError, AttributeError):
        return False
    left, right = produced.created_at, consumed.created_at
    if left.tzinfo is None:
        left = left.replace(tzinfo=UTC)
    if right.tzinfo is None:
        right = right.replace(tzinfo=UTC)
    return left < right


def _run_quality(services: Services, conditioned, daily) -> dict[str, Any]:
    """Both quality models: one over the readings used, one over the stream.

    Run before the feature pipeline because the decision engine reads the
    measurement-quality table, and an assessment that cannot see which
    instruments are suspect is the assessment this whole layer exists to
    prevent.
    """
    telemetry = services.datasets.datasets.get_by_name(F.TELEMETRY_DATASET)
    return {
        "measurement": _run_model(
            services, F.QUALITY_MODEL, conditioned, kind="evaluation"
        ),
        "sampling": _run_model(
            services, F.SAMPLING_MODEL, telemetry, kind="evaluation"
        ),
    }


# --------------------------------------------------------------------------
# charts
# --------------------------------------------------------------------------
def _charts() -> list[dict[str, Any]]:
    """Twenty readings of the fleet, grouped by the question they answer."""
    return [
        # ------------------------------------------------------ fleet
        {
            "group": "fleet", "source": "decisions",
            "name": "各廠區健康分數分布",
            "description": "Health score spread per site",
            "spec": {
                "chart_type": "box", "x": "site_name", "y": ["health_score"],
                "x_title": "廠區", "y_title": "健康分數", "unit": "分",
                "subtitle": (
                    "盒為四分位距；中位數之外還要看離散度——"
                    "一個廠區的平均值可能被一台設備拉走"
                ),
            },
        },
        {
            "group": "fleet", "source": "decisions",
            "name": "風險等級分布",
            "description": "How many assets sit at each risk level",
            "spec": {
                "chart_type": "bar", "x": "risk_level", "y": ["health_score"],
                "aggregation": "count",
                "x_order": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "x_title": "風險等級", "y_title": "設備數", "unit": "台",
                "value_labels": True,
                "subtitle": "風險 = 可能性 × 後果；後果來自設備重要程度，不是分數高低",
            },
        },
        {
            "group": "fleet", "source": "decisions",
            "name": "健康狀態 × 重要程度",
            "description": "Where the degraded assets are, against how much they matter",
            "spec": {
                "chart_type": "heatmap", "x": "criticality",
                "series": "health_status", "y": ["health_score"],
                "aggregation": "count",
                "x_order": ["low", "medium", "high", "critical"],
                "series_order": ["HEALTHY", "WATCH", "DEGRADED", "POOR", "CRITICAL"],
                "x_title": "重要程度", "y_title": "設備數", "unit": "台",
                "subtitle": "右下角是最需要注意的格子：重要且已劣化",
            },
        },
        {
            "group": "fleet", "source": "decisions",
            "name": "處置優先順序",
            "description": "Priority mix across the fleet",
            "spec": {
                "chart_type": "pie", "x": "priority", "y": ["health_score"],
                "aggregation": "count",
                "x_order": ["IMMEDIATE", "HIGH", "MEDIUM", "LOW", "NONE"],
                "x_title": "優先順序", "y_title": "設備數", "unit": "台",
                "subtitle": "NONE 佔多數才是正常的機隊——全部都要處置代表門檻設錯了",
            },
        },
        {
            "group": "fleet", "source": "decisions",
            "name": "各設備類型平均健康分數",
            "description": "Mean health by equipment class",
            "spec": {
                "chart_type": "bar", "x": "asset_type_label", "y": ["health_score"],
                "aggregation": "mean",
                "x_title": "設備類型", "y_title": "平均健康分數", "unit": "分",
                "value_labels": True,
                "subtitle": "類型之間的差異多半來自量測項目多寡，而不是可靠度本身",
            },
        },
        {
            "group": "fleet", "source": "decisions",
            "name": "關注度與健康分數的關係",
            "description": "Concern against health, one point per asset",
            "spec": {
                "chart_type": "scatter", "x": "concern_score", "y": ["health_score"],
                "sort_by": "concern_score",
                "x_title": "證據關注度", "y_title": "健康分數", "unit": "分",
                "subtitle": (
                    "兩者由不同途徑算出：健康來自特徵，關注度來自證據。"
                    "落在對角線外的個案值得看"
                ),
            },
        },
        # --------------------------------------------------- evidence
        {
            "group": "evidence", "source": "evidence",
            "name": "各分析器提出的證據數",
            "description": "How much each analyzer found",
            "spec": {
                "chart_type": "bar", "x": "analyzer", "y": ["contribution"],
                "aggregation": "count",
                "x_title": "分析器", "y_title": "證據筆數", "unit": "筆",
                "value_labels": True,
                "subtitle": "十個分析器不是十個門檻；提出證據多不代表貢獻大",
            },
        },
        {
            "group": "evidence", "source": "evidence",
            "name": "各分析器的平均貢獻",
            "description": "Mean contribution to the conclusion, per analyzer",
            "spec": {
                "chart_type": "bar", "x": "analyzer", "y": ["contribution"],
                "aggregation": "mean",
                "x_title": "分析器", "y_title": "平均貢獻", "unit": "點",
                "subtitle": "資料品質的貢獻是負的——它的作用是阻止對可疑讀值採取行動",
            },
        },
        {
            "group": "evidence", "source": "evidence",
            "name": "證據類別組成（依風險等級）",
            "description": "What kind of evidence sits behind each risk level",
            "spec": {
                "chart_type": "stacked_bar", "x": "risk_level", "series": "category",
                "y": ["contribution"], "aggregation": "count",
                "x_order": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "x_title": "風險等級", "y_title": "證據筆數", "unit": "筆",
                "subtitle": "高風險個案的證據來自多個類別，這正是它可信的原因",
            },
        },
        {
            "group": "evidence", "source": "evidence",
            "name": "證據貢獻分布",
            "description": "Distribution of evidence contributions",
            "spec": {
                "chart_type": "histogram", "y": ["contribution"], "bins": 20,
                "x_title": "單筆證據的貢獻", "y_title": "證據筆數", "unit": "筆",
                "subtitle": (
                    "左側的負值是反對採取行動的證據；"
                    "一個只會累加的系統最後會標記所有設備"
                ),
            },
        },
        # -------------------------------------------------- condition
        {
            "group": "condition", "source": "features",
            "name": "門檻進度分布",
            "description": "How far measurements sit from their emergency limit",
            "spec": {
                "chart_type": "histogram", "y": [F.C_LIMIT_PROGRESS], "bins": 24,
                "x_title": "門檻進度（0 = 如預期，100 = 緊急界線）",
                "y_title": "每日量測數", "unit": "筆",
                "subtitle": "健康的機隊集中在 0 附近；右尾就是需要注意的量測",
            },
        },
        {
            "group": "condition", "source": "features",
            "name": "各量測的門檻狀態組成",
            "description": "Threshold status mix per measurement",
            "spec": {
                "chart_type": "stacked_bar", "x": F.C_PARAMETER,
                "series": F.C_STATUS, "y": [F.C_LIMIT_PROGRESS],
                "aggregation": "count",
                "series_order": ["normal", "warning", "critical", "emergency"],
                "x_title": "量測項目", "y_title": "每日量測數", "unit": "筆",
                "subtitle": "界線是相對於應有值的偏移，因此不同負載的設備可以放在一起比",
            },
        },
        {
            "group": "condition", "source": "features",
            "name": "負載與偏差的關係",
            "description": "Deviation against load — does load explain it?",
            "spec": {
                "chart_type": "scatter", "x": F.C_LOAD, "y": [F.C_DEVIATION],
                "sort_by": F.C_LOAD, "limit": 500,
                "x_title": "當日平均負載 (%)", "y_title": "偏離應有值", "unit": "%",
                "subtitle": (
                    "殘差已經扣掉負載，所以這張圖應該看不出斜率——"
                    "看得出來就代表響應模型需要重配"
                ),
            },
        },
        {
            "group": "condition", "source": "features",
            "name": "各設備類型的門檻進度離散度",
            "description": "Limit progress spread per equipment class",
            "spec": {
                "chart_type": "box", "x": F.C_TYPE, "y": [F.C_LIMIT_PROGRESS],
                "x_title": "設備類型", "y_title": "門檻進度", "unit": "%",
                "subtitle": "鬚線外的點是個別設備的個別日子，不是整個類型的問題",
            },
        },
        # ---------------------------------------------------- quality
        {
            "group": "quality", "source": "quality",
            "name": "量測資料品質分數分布",
            "description": "Distribution of measurement quality scores",
            "spec": {
                "chart_type": "histogram", "y": ["quality_score"], "bins": 20,
                "x_title": "資料品質分數", "y_title": "量測序列數", "unit": "條",
                "subtitle": "在相信任何結論之前先看這張圖：低分的序列會讓上面每一層都失效",
            },
        },
        {
            "group": "quality", "source": "quality",
            "name": "各量測的品質旗標組成",
            "description": "Quality flags per measurement type",
            "spec": {
                "chart_type": "stacked_bar", "x": "parameter",
                "series": "quality_flag", "y": ["quality_score"],
                "aggregation": "count",
                "series_order": ["good", "acceptable", "suspect", "bad"],
                "x_title": "量測項目", "y_title": "序列數", "unit": "條",
                "subtitle": "卡死、位準跳動與離群值，逐一量測項目看",
            },
        },
        {
            "group": "quality", "source": "sampling",
            "name": "取樣缺漏率分布",
            "description": "Missing-reading rate across the raw stream",
            "spec": {
                "chart_type": "histogram", "y": ["missing_pct"], "bins": 16,
                "x_title": "缺漏比例", "y_title": "量測序列數", "unit": "條",
                "subtitle": "對原始串流而非清理後的資料計算——清理過的資料當然沒有缺漏",
            },
        },
        {
            "group": "quality", "source": "sampling",
            "name": "取樣間隔異常",
            "description": "Sampling gaps against the declared interval",
            "spec": {
                "chart_type": "scatter", "x": "readings", "y": ["gaps"],
                "sort_by": "readings",
                "x_title": "讀值筆數", "y_title": "超過宣告間隔的次數", "unit": "次",
                "subtitle": "落在上方的序列曾經斷線；用平均值描述那段期間會描述錯一段時間",
            },
        },
        # ---------------------------------------------------- history
        {
            "group": "history", "source": "maintenance",
            "name": "各維護類型的支出",
            "description": "Spend by maintenance type",
            "spec": {
                "chart_type": "bar", "x": "maintenance_type", "y": ["cost"],
                "aggregation": "sum",
                "x_title": "維護類型", "y_title": "累計成本", "unit": "元",
                "value_labels": True,
                "subtitle": "矯正維修單次成本遠高於預防保養——這是預知保養的整個經濟論證",
            },
        },
        {
            "group": "history", "source": "maintenance",
            "name": "停機時數分布（依維護類型）",
            "description": "Downtime spread by maintenance type",
            "spec": {
                "chart_type": "box", "x": "maintenance_type", "y": ["downtime_hours"],
                "x_title": "維護類型", "y_title": "停機時數", "unit": "小時",
                "subtitle": "計畫性維護的停機時間短且可預測；非計畫性的兩者都不是",
            },
        },
        {
            "group": "history", "source": "failures",
            "name": "失效模式次數",
            "description": "How often each failure mode occurs",
            "spec": {
                "chart_type": "bar", "x": "failure_type_label",
                "y": ["downtime_hours"], "aggregation": "count",
                "x_title": "失效模式", "y_title": "發生次數", "unit": "次",
                "value_labels": True,
                "subtitle": "型態比對分析器就是拿這些模式的訊號組合去比對現況",
            },
        },
        {
            "group": "history", "source": "failures",
            "name": "各嚴重度的停機時數",
            "description": "Downtime by failure severity",
            "spec": {
                "chart_type": "box", "x": "severity", "y": ["downtime_hours"],
                "x_order": ["minor", "major", "critical"],
                "x_title": "嚴重度", "y_title": "停機時數", "unit": "小時",
                "subtitle": "後果的量級差異，正是風險矩陣裡「後果」軸的依據",
            },
        },
    ]


def _seed_charts(services: Services, sources: dict[str, Any]) -> dict[str, str]:
    """Create every chart whose dataset exists, then one dashboard per group."""
    existing = {viz.name: viz.id for viz in services.visualizations.list()}
    by_group: dict[str, list[str]] = {}
    created: dict[str, str] = {}

    for chart in _charts():
        dataset = sources.get(chart["source"])
        if dataset is None or not getattr(dataset, "current_version_id", None):
            logger.info(
                "chart '%s' skipped: its dataset (%s) is not there",
                chart["name"], chart["source"],
            )
            continue
        name = chart["name"]
        viz_id = existing.get(name)
        if viz_id is None:
            try:
                viz_id = services.visualizations.create(
                    name=name,
                    description=chart["description"],
                    dataset_version_id=dataset.current_version_id,
                    spec=chart["spec"],
                ).id
            except FluxError as exc:
                logger.warning("could not seed the chart '%s': %s", name, exc)
                continue
        created[name] = viz_id
        by_group.setdefault(chart["group"], []).append(viz_id)

    for board in DASHBOARDS:
        tiles = by_group.get(board["group"], [])
        if not tiles or services.dashboards.repository.get_by_name(board["name"]):
            continue
        try:
            services.dashboards.create(
                name=board["name"],
                description=board["description"],
                tiles=[
                    {
                        "visualization_id": viz_id,
                        "x": (index % 2) * 6,
                        "y": (index // 2) * 4,
                        "width": 6,
                        "height": 4,
                    }
                    for index, viz_id in enumerate(tiles)
                ],
            )
            logger.info("seeded the '%s' dashboard", board["name"])
        except FluxError as exc:
            logger.warning("could not seed the '%s' dashboard: %s", board["name"], exc)
    return created


# --------------------------------------------------------------------------
# the experiment: five policies, one question
# --------------------------------------------------------------------------
def _seed_experiment(services: Services, features) -> dict[str, Any]:
    """Does the layered analysis actually beat a threshold alarm?

    One model and a dial, which is the more common shape of a comparison: the
    same engine at five settings, scored against what actually happened. F1 is
    the primary metric because a maintenance fleet is mostly healthy — accuracy
    would reward a policy that flags nothing.
    """
    model = services.models.repository.get_by_name(BACKTEST_MODEL)
    if model is None:
        try:
            model = services.models.create(
                name=BACKTEST_MODEL,
                provider="asset-maintenance-backtest",
                description=(
                    "在歷史上的每個評估日重跑決策政策，對照實際故障計算"
                    "精確率、召回率、F1 與平均提前天數。"
                ),
                configuration={
                    "horizon_days": BACKTEST_HORIZON_DAYS,
                    "step_days": BACKTEST_STEP_DAYS,
                    "warmup_days": 35,
                },
                tags=["maintenance", "validation"],
            )
        except FluxError as exc:
            logger.warning("could not seed the backtest model: %s", exc)
            return {}

    if features is None or not features.current_version_id:
        return {}

    experiment = services.experiments.repository.get_by_name(EXPERIMENT_NAME)
    if experiment is None:
        try:
            experiment = services.experiments.create(
                name=EXPERIMENT_NAME,
                description=(
                    "同一個引擎，五種分析組合，對同一份歷史資料評分。"
                    "問題不是「AI 比較準嗎」，而是「多加一層分析換到了什麼」。"
                ),
                objective="maximise F1 on a 14-day maintenance horizon",
                primary_metric="f1",
                primary_direction="higher",
                dataset_version_id=features.current_version_id,
                trials=[
                    {
                        "target_id": model.id,
                        "target_type": "model",
                        "label": policy.label,
                        "kind": "evaluation",
                        "parameters": {
                            "policy": key,
                            "horizon_days": BACKTEST_HORIZON_DAYS,
                            "step_days": BACKTEST_STEP_DAYS,
                        },
                    }
                    for key, policy in POLICIES.items()
                ],
            )
        except FluxError as exc:
            logger.warning("could not seed the policy experiment: %s", exc)
            return {}

    if not experiment.execution_ids or _stale(services, experiment, features):
        if (
            features.current_version_id
            and experiment.dataset_version_id != features.current_version_id
        ):
            experiment = services.experiments.update(
                experiment.id,
                {"dataset_version_id": features.current_version_id},
            )
        #  An install seeded by an earlier version carries scores from an
        #  earlier definition of what a correct answer is. Leaving them is
        #  worse than having none: a leaderboard whose numbers came from two
        #  different questions reads as one comparison and is not.
        try:
            experiment = services.experiments.run(experiment.id, services.executions)
            logger.info("ran the policy comparison: %s trials", len(experiment.trials))
        except FluxError as exc:
            logger.warning("could not run the policy comparison: %s", exc)
            return {}

    outcomes: dict[str, Any] = {}
    #  Newest first, so a re-run's scores are the ones the report and the
    #  evaluations pick up rather than the ones they replaced.
    for execution_id in reversed(experiment.execution_ids):
        try:
            execution = services.executions.get(execution_id)
        except FluxError:
            continue
        policy = str(execution.parameters.get("policy") or "")
        if policy in outcomes:
            continue
        outcomes[policy] = execution
        already = [
            e for e in services.evaluations.list(model_id=model.id)
            if e.execution_id == execution.id
        ]
        if already:
            continue
        try:
            services.evaluations.record(
                execution_id=execution.id,
                metrics=execution.metrics,
                #  The bar: better than one in two of the work orders being
                #  useful. Below that the workshop stops reading the alerts,
                #  and a system nobody reads has no recall at all.
                target={"metric": "precision", "min": 0.5},
                model_id=model.id,
                experiment_id=experiment.id,
                notes=(
                    f"政策「{POLICIES[policy].label if policy in POLICIES else policy}」"
                    f"在 {BACKTEST_HORIZON_DAYS} 天視野下的回測。"
                    "門檻設在精確率 0.5：低於此值，維修班會開始忽略警示。"
                ),
            )
        except FluxError as exc:
            logger.warning("could not record the evaluation for '%s': %s", policy, exc)
    return outcomes


#  A metric the current backtest always reports. Its absence in a recorded
#  execution is how an older *version of the scoring code* is recognised.
_CURRENT_METRIC = "episode_recall"


def _stale(services: Services, experiment, features) -> bool:
    """Whether this experiment's scores are still the current answer.

    Two ways they stop being. The scoring code can change — a new metric, a
    different definition of a correct answer — and the recorded runs then come
    from a question nobody is asking any more. Or the data can change, which
    for an experiment means the version it is pinned to is no longer the
    version everything else reads.

    Pinning is right: it is what makes a comparison mean the same thing next
    month. But a *seeded example* is meant to describe the fleet as it is, so
    when the data moves the example moves with it — deliberately, and by
    re-running rather than by editing the scores it had.
    """
    if features is not None and features.current_version_id:
        if experiment.dataset_version_id != features.current_version_id:
            return True
    for execution_id in experiment.execution_ids:
        try:
            execution = services.executions.get(execution_id)
        except FluxError:
            continue
        if _CURRENT_METRIC in (execution.metrics or {}):
            return False
    return True


def _seed_schedule(services: Services) -> None:
    """Re-assess the fleet every morning, before the shift meeting."""
    model = services.models.repository.get_by_name(DECISION_MODEL)
    if model is None or services.schedules.repository.get_by_name(SCHEDULE_NAME):
        return
    try:
        services.schedules.create(
            name=SCHEDULE_NAME,
            target_id=model.id,
            kind="calculation",
            cron="0 5 * * *",
            description=(
                "每天 05:00 重跑整廠評估，讓交接班會議看到的是當天的狀態，"
                "而不是上一次有人按下按鈕時的狀態。"
            ),
            parameters={"policy": "full_risk_adjusted"},
        )
        logger.info("seeded the daily fleet assessment schedule")
    except FluxError as exc:
        logger.warning("could not seed the assessment schedule: %s", exc)


# --------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------
def _chart_section(charts: dict[str, str], name: str, title: str) -> list[dict]:
    viz_id = charts.get(name)
    return [{"kind": "chart", "title": title, "visualization_id": viz_id}] if viz_id else []


def _seed_fleet_report(
    services: Services, charts: dict[str, str], decisions, evidence, reasoning
) -> None:
    """The document a maintenance manager reads on Monday morning."""
    if services.reports.repository.get_by_name(FLEET_REPORT):
        return

    sections: list[dict[str, Any]] = [
        {
            "kind": "text",
            "title": "一、這份報告在回答什麼",
            "body": (
                "本報告回答四個問題：**哪些設備現在需要處置**、**為什麼**、"
                "**多久之內要做**，以及**這個判斷有多可信**。\n\n"
                "它不是異常偵測報表。異常偵測只回答第一個問題的一部分，而且回答得"
                "不夠好：一個讀值偏高，可能是設備劣化，也可能是負載變高、廠房變熱，"
                "或者感測器壞了。這三種情況的處置完全不同，而它們在單一讀值上看起來"
                "一模一樣。\n\n"
                "分析的順序是：先把讀值放回它的工況（負載、環境、運轉狀態），算出"
                "「這個工況下本來應該讀到多少」，再看實際偏離多少；接著檢查趨勢、"
                "統計顯著性、是否符合某個已知失效模式的訊號組合、保養週期消耗了多少、"
                "歷史上同型設備發生過什麼，以及——這一層最容易被略過——這些讀值本身"
                "可不可信。"
            ),
        },
        {
            "kind": "text",
            "title": "二、本期機隊狀態",
            "body": (
                "下列圖表為全機隊四十台設備的當期評估。閱讀時建議的順序是：\n\n"
                "1. **風險等級分布**看整體壓力，"
                "2. **健康狀態 × 重要程度**找出「重要且已劣化」的格子，"
                "3. **處置優先順序**確認待辦量是否合理。\n\n"
                "如果「需要處置」的比例超過兩成，通常不是機隊突然變差，而是門檻"
                "或權重設定需要檢討——警示疲勞會讓整套系統失效，這比漏掉一台設備"
                "更難修復。"
            ),
        },
    ]
    for name, title in (
        ("風險等級分布", "風險等級"),
        ("健康狀態 × 重要程度", "重要程度與健康狀態"),
        ("處置優先順序", "處置優先順序"),
        ("各廠區健康分數分布", "各廠區的健康分數"),
        ("各設備類型平均健康分數", "各設備類型"),
    ):
        sections += _chart_section(charts, name, title)

    if decisions and decisions.current_version_id:
        sections.append(
            {
                "kind": "table",
                "title": "全機隊評估結果",
                "dataset_version_id": decisions.current_version_id,
                "options": {"limit": 40},
            }
        )

    sections.append(
        {
            "kind": "text",
            "title": "三、判斷依據",
            "body": (
                "每一項結論都可以追到提出它的分析器與具體數字。下面兩張圖說明"
                "各分析器的角色：\n\n"
                "**證據數量不等於影響力。** 門檻分析器提出的證據最少，但單筆權重最高；"
                "趨勢與基線分析器提出得多，但每一筆的份量較輕，靠的是累積。\n\n"
                "**資料品質的平均貢獻是負的。** 這是刻意的設計。一支卡住的感測器是"
                "「不要對這個讀值採取行動」的理由，不是「情況更嚴重」的理由。"
                "一個只會累加關注度的系統，最後會把整個機隊都標記起來。"
            ),
        }
    )
    for name, title in (
        ("各分析器提出的證據數", "各分析器的產出量"),
        ("各分析器的平均貢獻", "各分析器的影響力"),
        ("證據類別組成（依風險等級）", "高風險個案的證據結構"),
        ("證據貢獻分布", "正反兩面的證據"),
    ):
        sections += _chart_section(charts, name, title)

    if evidence and evidence.current_version_id:
        sections.append(
            {
                "kind": "table",
                "title": "逐條判斷依據（前 30 筆）",
                "dataset_version_id": evidence.current_version_id,
                "options": {"limit": 30},
            }
        )
    if reasoning and reasoning.current_version_id:
        sections.append(
            {
                "kind": "table",
                "title": "各設備的判斷說明",
                "dataset_version_id": reasoning.current_version_id,
                "options": {"limit": 12},
            }
        )

    sections += [
        {
            "kind": "text",
            "title": "四、狀態、門檻與趨勢",
            "body": (
                "門檻是**相對於應有值的偏移量**，不是固定值。這一點值得說明，因為"
                "它是整套分析能不能用在真實工廠的關鍵。\n\n"
                "一台在 45% 負載運轉的泵浦流量是 57 m³/h，在 90% 負載是 108 m³/h。"
                "任何能抓到葉輪磨蝕的固定流量下限，都會把前者永遠判成異常。把界線"
                "定在「比這個工況下的應有值低多少」，健康的設備在任何負載下都停在"
                "零附近，劣化的設備則會走開。\n\n"
                "**負載與偏差的關係圖應該看不出斜率。** 看得出來，就代表響應模型的"
                "係數需要重新配適——這張圖是分析本身的健檢。"
            ),
        }
    ]
    for name, title in (
        ("門檻進度分布", "全機隊距離界線的分布"),
        ("各量測的門檻狀態組成", "各量測項目的狀態"),
        ("負載與偏差的關係", "負載是否已被扣除"),
        ("各設備類型的門檻進度離散度", "各設備類型的離散度"),
    ):
        sections += _chart_section(charts, name, title)

    sections += [
        {
            "kind": "text",
            "title": "五、資料品質",
            "body": (
                "這一節放在結論之前而不是附錄，因為它決定了上面每一段能不能成立。\n\n"
                "檢查分兩個串流做，而且刻意不同：**取樣完整性**對原始資料檢查缺漏、"
                "重複與斷線；**量測品質**只對分析真正用到的讀值（機器運轉中的時段）"
                "檢查離群、卡死、位準跳動與漂移。混在一起做會得到一個「什麼都可疑」"
                "的機隊——一台日夜停機的設備，它的停機讀值會被判成離群值，它的夜間"
                "會被判成資料斷線。\n\n"
                "**位準跳動**是其中最有價值的一項：溫度變送器被改設成華氏，會呈現為"
                "一段完全合理的上升趨勢，而它是這類系統最昂貴的誤報來源。持續性的"
                "位準位移可以和尖峰雜訊、以及緩慢的真實劣化區分開來。"
            ),
        }
    ]
    for name, title in (
        ("量測資料品質分數分布", "量測品質分數"),
        ("各量測的品質旗標組成", "各量測項目的品質"),
        ("取樣缺漏率分布", "原始串流的缺漏"),
        ("取樣間隔異常", "斷線紀錄"),
    ):
        sections += _chart_section(charts, name, title)

    sections += [
        {
            "kind": "text",
            "title": "六、限制",
            "body": (
                "**一、本機隊為模擬產生。** 讀值由負載、環境與設備響應係數合成，"
                "劣化依宣告的失效模式演進。這樣做的唯一理由是：真實機隊沒有已知答案，"
                "而沒有已知答案就無法對決策政策評分。模擬真值只用於回測，不參與"
                "任何一次評估。\n\n"
                "**二、響應模型的係數是設計值。** 在真實安裝中，這些係數應該由設備"
                "自身一段乾淨的歷史配適出來，並在每次大修後重新配適。\n\n"
                "**三、維修紀錄不完整時，週期消耗無法計算。** 系統會明說「查無執行"
                "紀錄」，而不是把它當成「從未執行」——後者會讓全廠設備都顯示超期。\n\n"
                "**四、緩慢的單訊號感測器漂移仍是殘餘的誤判來源。** 系統會以"
                "「孤立偏離、缺乏旁證」提出質疑並建議先複量，但無法完全排除。"
            ),
        }
    ]

    try:
        services.reports.create(
            name=FLEET_REPORT,
            description="本期全機隊健康、風險與處置建議，含判斷依據與資料品質。",
            sections=sections,
            tags=["maintenance", "fleet"],
        )
        logger.info("seeded the fleet report")
    except FluxError as exc:
        logger.warning("could not seed the fleet report: %s", exc)


def _seed_validation_report(
    services: Services, charts: dict[str, str], backtests: dict[str, Any]
) -> None:
    """Whether the layered analysis is worth its complexity, measured.

    Rebuilt whenever the comparison behind it has been re-run. A report that
    names an execution keeps naming that execution for ever, which is right
    for a report somebody published and wrong for a seeded example whose whole
    job is to describe the current state — it went on quoting the first scores
    the platform ever produced while the leaderboard beside it showed the
    fourth, and both looked authoritative.
    """
    existing = services.reports.repository.get_by_name(VALIDATION_REPORT)
    current = {execution.id for execution in backtests.values()}
    if existing is not None:
        quoted = {
            section.execution_id
            for section in existing.sections
            if getattr(section, "execution_id", None)
        }
        if not current or quoted == current:
            return

    sections: list[dict[str, Any]] = [
        {
            "kind": "text",
            "title": "一、問題",
            "body": (
                "多加一層分析，換到了什麼？\n\n"
                "這個問題常被跳過，因為答案理所當然——但它其實不是。加分析器會同時"
                "提高召回率與誤報率，而誤報的代價不是零：每一張沒必要的工單都在消耗"
                "維修班對系統的信任，而失去信任的系統召回率等於零。\n\n"
                "所以本報告用同一個引擎、同一份資料、五種分析組合，在歷史上的每個"
                "評估日重跑一次，對照實際發生的故障評分。"
            ),
        },
        {
            "kind": "text",
            "title": "二、方法",
            "body": (
                f"**評估方式。** 自資料起始 35 天（滾動視窗需要的暖機期）之後，"
                f"每 {BACKTEST_STEP_DAYS} 天讓引擎看一次全機隊。每次只使用該日期"
                f"以前的資料。\n\n"
                "**判定標準是「這台設備當下是不是真的在劣化」，不是「它十四天內會不會"
                "壞」。** 這個區別決定了整份數字有沒有意義，而選錯是這類評估最常見的"
                "做法。\n\n"
                "用後者評分時，一個在軸承故障前六週就正確指認出來的引擎，會在除了"
                "最後兩次以外的每一個評估日都被判定為誤報；而一台在資料結束時仍在"
                "劣化中的設備，因為它的故障落在資料範圍外，會在每一個評估日都被判為"
                "誤報。實測下來，精確率會掉到 0.16 —— 而逐案檢視時引擎幾乎每次都是"
                "對的。針對那個數字最佳化，等於要求引擎**更晚**才察覺問題，"
                "正好與這套系統存在的目的相反。\n\n"
                "因此正例的定義是：該設備在該日期處於「真實劣化已開始、尚未修復」的"
                "區間內，取自模擬機隊自身的紀錄。在沒有這份紀錄的場合——也就是每一個"
                "真實廠區——回測會退回「視野內是否發生故障」的規則，並在 "
                "`label_basis` 欄位明白標示用的是哪一種。近似的標籤只要指名為近似，"
                "就是誠實的；不指名才不是。\n\n"
                f"最後 {BACKTEST_HORIZON_DAYS} 天不計分，因為還沒有時間發生任何事。\n\n"
                "**主要指標為 F1，不是準確率。** 機隊絕大多數時間是健康的，"
                "一個什麼都不標記的政策準確率可以到 0.95 而毫無用處。\n\n"
                "**平均提前天數與精確率並列。** 一個總在故障前一天才發現的系統，"
                "召回率完美而沒有價值。"
            ),
        },
    ]

    order = list(POLICIES)
    for key in order:
        execution = backtests.get(key)
        if execution is None:
            continue
        sections.append(
            {
                "kind": "metrics",
                "title": f"{POLICIES[key].label}",
                "execution_id": execution.id,
            }
        )

    sections += [
        {
            "kind": "text",
            "title": "三、兩種召回率",
            "body": (
                "報告同時列出兩個召回率，因為它們回答的不是同一個問題。\n\n"
                "**逐日召回率**問的是「在該設備確實處於劣化狀態的那些日子裡，"
                "系統有多少天判定需要處置」。它會把劣化剛開始、訊號還埋在雜訊裡的"
                "那幾週算成漏報——那幾週其實看不出東西，任何方法都看不出。\n\n"
                "**逐次召回率（episodes_detected / episodes）**問的是維修主管真正"
                "在意的事：「這一段劣化，我們到底有沒有抓到，以及提早了多久」。"
                "旁邊的平均提前天數就是答案的後半。\n\n"
                "兩個都列出來，是因為只看逐日召回率會低估一套「晚一點才確定、"
                "但每次都抓得到」的系統，而只看逐次召回率會高估一套「什麼都標記」"
                "的系統。第二個風險由精確率與警示率擋住。"
            ),
        },
        {
            "kind": "text",
            "title": "四、如何讀這些數字",
            "body": (
                "**僅門檻告警**是基準線，不是被推薦的做法。它只在讀值越線後才動作，"
                "因此對已經明顯的個案有效，對還在發展中的個案無效。\n\n"
                "**門檻＋保養政策**是多數廠區的現況：越線就修，加上按時數與日曆的"
                "定期保養。\n\n"
                "**統計與趨勢**開始看得到「還沒越線但正在移動」的設備。這是條件式"
                "維護的核心價值，也是門檻永遠做不到的事。\n\n"
                "**完整分析引擎**再加入失效型態比對、歷史比對、工程規則與資料品質。"
                "資料品質那一層的貢獻是負的——它的價值在精確率而不是召回率上，"
                "而精確率正是決定這套系統會不會被繼續使用的那個數字。\n\n"
                "**完整分析＋風險分級門檻**對關鍵設備採用較低的派工門檻。它的整體"
                "F1 不必然最高，但它把誤判的分布移到了對的地方：一台變壓器與一台"
                "抽風機不該用同一個證據門檻，用同一個就等於選擇要在哪一邊犯錯。"
            ),
        },
        {
            "kind": "text",
            "title": "五、限制",
            "body": (
                "**本次驗證使用模擬機隊。** 這是刻意的：唯有已知答案才能對政策評分，"
                "而真實機隊沒有。代價是這些分數衡量的是「引擎能否辨識它被設計要辨識的"
                "劣化型態」，而不是「它在貴廠的表現」。\n\n"
                "遷移到真實資料時，應該重新執行同一套回測，並預期：召回率下降"
                "（真實劣化較不規則）、精確率下降（真實資料的品質問題較多且較隱蔽）、"
                "提前天數的離散度上升。回測本身可以直接沿用，但**標籤會退回近似值**："
                "真實廠區沒有「劣化何時開始」的紀錄，只有「何時修的」。實務上可行的"
                "做法是從矯正性維修紀錄往前回推該失效模式的典型發展期，並把這個假設"
                "寫在報告裡，而不是藏在程式碼裡。\n\n"
                "**樣本數有限。** 觀測窗口內的故障事件為個位數到十餘件，"
                "因此政策之間的細微差距不具統計顯著性。可以下結論的是分層的方向性，"
                "不是某個政策精確到小數點後兩位的優勢。"
            ),
        },
    ]

    try:
        if existing is not None:
            services.reports.update(existing.id, {"sections": sections})
            logger.info("refreshed the validation report with the current scores")
        else:
            services.reports.create(
                name=VALIDATION_REPORT,
                description="五種決策政策在同一份歷史資料上的回測結果、讀法與限制。",
                sections=sections,
                tags=["maintenance", "validation"],
            )
            logger.info("seeded the validation report")
    except FluxError as exc:
        logger.warning("could not seed the validation report: %s", exc)
