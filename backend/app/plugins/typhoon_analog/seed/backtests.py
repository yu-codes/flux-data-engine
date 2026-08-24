"""The typhoon worked example.

Every capability the platform has, exercised once on the real CWA record of
typhoons affecting Taiwan, so a fresh install can be read rather than imagined:

    catalogue (440 rows, messy)
        -> Pipeline: twelve standard transforms (see climatology.py)
        -> clean analysis dataset
        -> sixteen charts across four dashboards
        -> Backtest models (leave-one-out validation of three analog methods)
        -> Experiment + Evaluations comparing them
        -> Schedule that re-validates every week
        -> Report written as a forecaster would read it

Kept out of `app/core/seed.py` because it is a domain example, not platform
bootstrap: everything here is created through the ordinary public services.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Services
from app.modules.execution.domain.ports import RunInline
from app.shared.errors import FluxError

from .climatology import seed_climatology_charts, seed_climatology_pipeline

logger = logging.getLogger(__name__)

CATALOGUE_DATASET = "Taiwan typhoon catalogue"

BACKTEST_RRF_MODEL = "Typhoon backtest · Coastline-RRF"
BACKTEST_KNN_MODEL = "Typhoon backtest · weighted KNN"
BACKTEST_BASELINE_MODEL = "Typhoon backtest · random baseline"
EXPERIMENT_NAME = "Analog method comparison"
SWEEP_NAME = "Coastline-RRF · k sweep"

#  What each trial is arguing, in the terms of the question being asked.
TRIAL_LABELS = {
    BACKTEST_RRF_MODEL: "Coastline-RRF (k=5, 500km buffer)",
    BACKTEST_KNN_MODEL: "Weighted KNN (k=5)",
    BACKTEST_BASELINE_MODEL: "Random baseline",
}
SCHEDULE_NAME = "Weekly typhoon re-validation"
REPORT_NAME = "Typhoon model validation"
CLIMATOLOGY_REPORT_NAME = "Taiwan typhoon climatology"

#  Small enough that a fresh install finishes seeding promptly; the UI can run
#  a larger sample on demand. Coastline methods cost roughly a second each.
SEED_SAMPLE_SIZE = 15


def seed_typhoon_example(services: Services) -> None:
    """Build the whole example. Safe to run again: every step checks first."""
    catalogue = services.datasets.datasets.get_by_name(CATALOGUE_DATASET)
    if not catalogue:
        logger.info("typhoon catalogue absent; skipping the worked example")
        return

    #  The example must exist in full by the time the API is up, so its
    #  executions run in-process even where the deployment uses a worker.
    services.executions.dispatcher = RunInline()

    #  The climatology half: cleaning steps, the pipeline, the charts.
    analysis_dataset = seed_climatology_pipeline(services, catalogue)
    charts = seed_climatology_charts(services, analysis_dataset) if analysis_dataset else {}

    #  The validation half: backtests, the comparison, the schedule, the reports.
    models = _seed_models(services)
    backtests = _seed_validation(services, models)
    _seed_sweep(services, models)
    _seed_schedule(services, models)
    _seed_report(services, catalogue, analysis_dataset, backtests)
    _seed_climatology_report(services, catalogue, analysis_dataset, charts)


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
def _seed_models(services: Services) -> dict[str, Any]:
    """The three backtest models the experiment compares."""
    definitions = [
        {
            "name": BACKTEST_RRF_MODEL,
            "provider": "typhoon-backtest",
            "description": (
                "Leave-one-out validation of Coastline-RRF: absolute-position "
                "ranking fused with weighted KNN."
            ),
            "configuration": {
                "method": "coastline_rrf",
                "k": 5,
                "buffer_km": 500.0,
                "sample_size": SEED_SAMPLE_SIZE,
            },
            "tags": ["typhoon", "validation"],
        },
        {
            "name": BACKTEST_KNN_MODEL,
            "provider": "typhoon-backtest",
            "description": "The same validation for weighted-KNN summary features.",
            "configuration": {
                "method": "knn_optimized",
                "k": 5,
                "sample_size": SEED_SAMPLE_SIZE,
            },
            "tags": ["typhoon", "validation"],
        },
        {
            "name": BACKTEST_BASELINE_MODEL,
            "provider": "typhoon-backtest",
            "description": (
                "Random analogs. The lower bound every other method has to beat "
                "before its score means anything."
            ),
            "configuration": {
                "method": "baseline",
                "k": 5,
                "sample_size": SEED_SAMPLE_SIZE,
            },
            "tags": ["typhoon", "validation"],
        },
    ]

    seeded: dict[str, Any] = {}
    for spec in definitions:
        existing = services.models.repository.get_by_name(spec["name"])
        if existing:
            seeded[spec["name"]] = existing
            continue
        try:
            seeded[spec["name"]] = services.models.create(**spec)
        except FluxError as exc:
            logger.warning("could not seed model '%s': %s", spec["name"], exc)
    return seeded


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def _seed_validation(services: Services, models: dict[str, Any]) -> dict[str, Any]:
    """Run each backtest once, then record the comparison as an Experiment."""
    names = [BACKTEST_RRF_MODEL, BACKTEST_KNN_MODEL, BACKTEST_BASELINE_MODEL]
    present = [name for name in names if models.get(name)]
    if not present:
        return {}

    trials = [
        {
            "model_id": models[name].id,
            "label": TRIAL_LABELS.get(name, name),
            "kind": "evaluation",
        }
        for name in present
    ]

    experiment = services.experiments.repository.get_by_name(EXPERIMENT_NAME)
    if experiment and not any(trial.label for trial in experiment.trials):
        #  An install that predates labelled trials was backfilled from bare
        #  model ids. Give the shipped example its labels back rather than
        #  leaving the one worked comparison less legible than a new one.
        experiment = services.experiments.update(experiment.id, {"trials": trials})
    if not experiment:
        experiment = services.experiments.create(
            name=EXPERIMENT_NAME,
            description=(
                "Do the geometric methods actually beat a random draw, and each "
                "other, on the CWA's own labels?"
            ),
            objective="maximise leave-one-out category accuracy",
            primary_metric="accuracy",
            #  Each trial names what is being compared rather than which model
            #  row it came from, so the leaderboard reads as an argument.
            trials=trials,
        )

    outcomes: dict[str, Any] = {}
    for name in present:
        model = models[name]
        #  This experiment's own runs, not merely the model's most recent one.
        #  The k sweep runs the same model at other settings, so "latest
        #  execution of this model" started returning a sweep trial and the
        #  comparison quietly reported k=9's score as the baseline's.
        previous = services.executions.list(
            model_id=model.id, experiment_id=experiment.id, limit=1
        )
        execution = previous[0] if previous else None
        if not execution or execution.status.value != "succeeded":
            try:
                execution = services.executions.submit(
                    model_id=model.id,
                    kind="evaluation",
                    experiment_id=experiment.id,
                )
            except FluxError as exc:
                logger.warning("backtest '%s' failed: %s", name, exc)
                continue

        if execution.status.value != "succeeded":
            logger.warning("backtest '%s' finished %s", name, execution.status.value)
            continue

        services.experiments.attach_execution(experiment.id, execution.id)
        #  An Evaluation states the bar; the backtest states the score.
        already = [
            e for e in services.evaluations.list(model_id=model.id)
            if e.execution_id == execution.id
        ]
        if not already:
            services.evaluations.record(
                execution_id=execution.id,
                metrics=execution.metrics,
                target={"metric": "accuracy", "min": 0.5},
                model_id=model.id,
                experiment_id=experiment.id,
                notes=(
                    "Leave-one-out over the historical record. The bar is set at "
                    "0.5: anything below that is not usefully better than guessing "
                    "between the two commonest classes."
                ),
            )
        outcomes[name] = execution
        logger.info(
            "backtest '%s': accuracy %s over %s typhoons",
            name, execution.metrics.get("accuracy"), execution.metrics.get("total"),
        )
    return outcomes


def _seed_sweep(services: Services, models: dict[str, Any]) -> None:
    """One model, three settings: what a trial is for.

    The method comparison puts three different models side by side. This one
    puts a single model against itself at three values of k, which is the case
    a bare list of model ids could not express at all - and it is the more
    common question, since most tuning is one model and a dial.
    """
    model = models.get(BACKTEST_RRF_MODEL)
    if not model or services.experiments.repository.get_by_name(SWEEP_NAME):
        return

    experiment = services.experiments.create(
        name=SWEEP_NAME,
        description=(
            "How many historical analogs should a vote draw on? Too few and one "
            "odd track decides it; too many and the neighbourhood stops being a "
            "neighbourhood."
        ),
        objective="find the k that maximises leave-one-out accuracy",
        primary_metric="accuracy",
        trials=[
            {
                "model_id": model.id,
                "label": f"k = {k}",
                "kind": "evaluation",
                "parameters": {"k": k, "sample_size": SEED_SAMPLE_SIZE},
            }
            for k in (3, 5, 9)
        ],
    )
    try:
        services.experiments.run(experiment.id, services.executions)
        logger.info("seeded the k sweep: %s trials", len(experiment.trials))
    except FluxError as exc:
        logger.warning("could not run the k sweep: %s", exc)


def _seed_schedule(services: Services, models: dict[str, Any]) -> None:
    """Re-validate weekly, so the score never silently goes stale."""
    model = models.get(BACKTEST_RRF_MODEL)
    if not model or services.schedules.repository.get_by_name(SCHEDULE_NAME):
        return
    try:
        services.schedules.create(
            name=SCHEDULE_NAME,
            target_id=model.id,
            kind="evaluation",
            cron="0 4 * * 1",
            description=(
                "Mondays at 04:00: re-runs the Coastline-RRF backtest so the "
                "published accuracy always reflects the current code and data."
            ),
            parameters={"sample_size": 40},
        )
        logger.info("seeded the weekly re-validation schedule")
    except FluxError as exc:
        logger.warning("could not seed the re-validation schedule: %s", exc)


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def _seed_report(
    services: Services, catalogue, analysis_dataset, backtests: dict[str, Any]
) -> None:
    if services.reports.repository.get_by_name(REPORT_NAME):
        return

    sections: list[dict[str, Any]] = [
        {
            "kind": "text",
            "title": "What this report says",
            "body": (
                "The typhoon analog model predicts which of the CWA's nine "
                "landfall-track classes an incoming typhoon belongs to, by "
                "finding the historical typhoons whose tracks pass closest to "
                "it.\n\n"
                "Nothing here is trained. The model is validated the way an "
                "analog method has to be: leave-one-out over the historical "
                "record, with each typhoon excluded from its own candidate pool."
            ),
        }
    ]

    rrf = backtests.get(BACKTEST_RRF_MODEL)
    if rrf:
        sections += [
            {
                "kind": "metrics",
                "title": "Coastline-RRF, leave-one-out",
                "execution_id": rrf.id,
            },
            {
                "kind": "table",
                "title": "Every scored typhoon",
                "result_id": rrf.result_id,
                "options": {"limit": 25},
            },
        ]

    baseline = backtests.get(BACKTEST_BASELINE_MODEL)
    if baseline:
        sections.append(
            {
                "kind": "metrics",
                "title": "Random baseline, for comparison",
                "execution_id": baseline.id,
            }
        )

    charts = {viz.name: viz.id for viz in services.visualizations.list()}
    #  The population the model is scored against, and the impact that makes
    #  getting the class right worth doing.
    featured = (
        "路徑分類佔比",
        "各路徑分類的風速分布",
    )
    for name in featured:
        if name in charts:
            sections.append(
                {"kind": "chart", "title": name, "visualization_id": charts[name]}
            )

    if analysis_dataset and analysis_dataset.current_version_id:
        sections.append(
            {
                "kind": "table",
                "title": "Cleaned catalogue",
                "dataset_version_id": analysis_dataset.current_version_id,
                "options": {"limit": 10},
            }
        )

    try:
        services.reports.create(
            name=REPORT_NAME,
            description=(
                "How the analog model scores against the CWA's own labels, and "
                "what the historical record it draws on looks like."
            ),
            sections=sections,
            tags=["typhoon", "validation"],
        )
        logger.info("seeded the typhoon validation report")
    except FluxError as exc:
        logger.warning("could not seed the typhoon report: %s", exc)


# --------------------------------------------------------------------------
# climatology report
# --------------------------------------------------------------------------
def _climatology_sections(charts: dict[str, str]) -> list[dict[str, Any]]:
    """The report as a forecaster would write it: claim, evidence, caveat.

    Charts are referenced by name; any that failed to seed is simply left out,
    so a partial install still produces a readable document.
    """

    def chart(name: str, title: str) -> list[dict[str, Any]]:
        viz_id = charts.get(name)
        if not viz_id:
            return []
        return [{"kind": "chart", "title": title, "visualization_id": viz_id}]

    sections: list[dict[str, Any]] = [
        {
            "kind": "text",
            "title": "一、資料與範圍",
            "body": (
                "本報告的母體是中央氣象署（CWA）發布過警報、並經人工判定「侵臺路"
                "徑分類」的颱風個案，時間自 1958 年起。原始目錄共 440 筆，其中具"
                "有路徑分類者為分析母體；未分類者多為僅在外海活動、未達發布標準的"
                "個案，保留在原始目錄中但不進入統計。\n\n"
                "所有數值欄位都經過一次性的型別轉換：近中心最大風速原本以「30 (公"
                "尺/秒)」這類文字記錄，事件累積雨量與中心氣壓亦混雜文字與空值。清"
                "理在 Pipeline 中完成，每一步都是可重跑、可追溯的 Execution。"
            ),
        },
    ]

    sections += [
        {
            "kind": "text",
            "title": "二、發生頻率與季節性",
            "body": (
                "侵臺颱風的年際變動遠大於任何長期趨勢：單一年份的個數從 0 到 8 都"
                "出現過，因此以少數幾年的高低判斷「是否變多」並不可靠，至少需要 "
                "10 年以上的滑動平均才談得上趨勢。\n\n"
                "季節性則非常穩定。生成月份集中在 7 至 9 月，這三個月占絕大多數；"
                "5 月與 11 月屬邊緣季節，個案少但一旦發生往往路徑異常。作業上的意"
                "義是：防災資源的配置應以七至九月為核心，而非平均分攤全年。"
            ),
        }
    ]
    sections += chart("每年侵臺颱風個數", "年際變動")
    sections += chart("生成月份分布", "季節分布")
    sections += chart("各年代強度組成", "各年代的強度組成")

    sections += [
        {
            "kind": "text",
            "title": "三、強度結構",
            "body": (
                "以中央氣象署的強度分級（輕度 17.2–32.6 m/s、中度 32.7–50.9 m/s、"
                "強烈 ≥51.0 m/s）來看，風速分布呈現明顯的多峰形狀，峰值落在各級距"
                "的中段——這反映的是強度判定本身的離散化，而不是自然界的真實分布，"
                "解讀直方圖時必須把這一點考慮進去。\n\n"
                "風速與中心氣壓之間維持著熱帶氣旋最穩定的經驗關係：氣壓越低、風速"
                "越高，散布圖上呈現清楚的負相關。少數偏離主帶的個案通常來自早期觀"
                "測，其氣壓值可能是估計而非實測。\n\n"
                "各路徑分類之間的強度差異，用盒鬚圖比用平均值誠實：中位數的差距往"
                "往小於四分位距，也就是說「哪一類比較強」在統計上並不像平均值看起"
                "來那麼確定。"
            ),
        }
    ]
    sections += chart("近中心最大風速分布", "風速分布")
    sections += chart("中心最低氣壓分布", "氣壓分布")
    sections += chart("風速–氣壓關係", "風速與氣壓的經驗關係")
    sections += chart("各路徑分類的風速分布", "各路徑分類的強度離散度")
    sections += chart("生命期時數分布", "生命期分布")

    sections += [
        {
            "kind": "text",
            "title": "四、降水影響",
            "body": (
                "雨量採中央氣象署的分級門檻（大雨 80 mm、豪雨 200 mm、大豪雨 350 "
                "mm、超大豪雨 500 mm），但此處使用的是「事件累積量」而非 24 小時累"
                "積量，因此分級只作為量級參考，不等同於作業發布的雨量特報標準。\n\n"
                "降水分布強烈右偏：多數個案雨量不高，少數個案極端。這正是必須用中"
                "位數與四分位距、而不是平均值來描述降水的原因——一場超大豪雨就足以"
                "把整個類別的平均值拉高到不具代表性。\n\n"
                "以臺南與高雄兩站並列比較，可以看出西南部的致雨路徑並不是強度最強"
                "的路徑，而是移速慢、且能把西南氣流引進來的路徑。強度與降水是兩個"
                "需要分開評估的風險維度。"
            ),
        }
    ]
    sections += chart("各強度的臺南事件雨量分布", "強度與雨量的關係")
    sections += chart("各路徑分類平均雨量（臺南 / 高雄）", "兩站的路徑致雨差異")
    sections += chart("雨量分級組成（依強度）", "雨量分級組成")

    sections += [
        {
            "kind": "text",
            "title": "五、路徑與登陸特徵",
            "body": (
                "生成位置集中在西北太平洋，經度越靠西的生成點抵臺時間越短、預警窗"
                "口越窄。登陸比例則清楚地跟著路徑分類走：穿越本島的第 2、3、4 類幾"
                "乎全數登陸，而在海面通過的第 1、5、7 類登陸比例極低——這正是路徑"
                "分類作為預測標籤的價值所在，它同時決定了風、雨與登陸三種風險。\n\n"
                "海上警報時數隨強度拉長，但離散度很大：警報長度取決於移速與距離，"
                "強度只是其中一個因素。把警報時數當成強度的代理變數會有系統性偏差。"
            ),
        }
    ]
    sections += chart("生成位置分布", "生成位置")
    sections += chart("各分類登陸比例", "各路徑分類的登陸比例")
    sections += chart("月份 × 路徑分類熱區圖", "季節與路徑的交互")
    sections += chart("海上警報時數 vs 風速", "警報時數與強度")

    sections += [
        {
            "kind": "text",
            "title": "六、作業意涵與資料限制",
            "body": (
                "**作業意涵。** 路徑分類是最有作業價值的單一標籤：它一次決定了登陸"
                "機率、影響區域與致雨型態。這也是本平台把類比預測的目標設定為路徑"
                "分類、而非單純預測強度的原因。\n\n"
                "**資料限制。** 一、早期（1970 年代以前）的氣壓與陣風紀錄大量缺"
                "漏，任何跨年代比較都必須說明樣本數差異，不能直接對比。二、觀測系"
                "統與定強方法歷經多次變更，強度序列並非同質，因此本報告不對長期強"
                "度趨勢下結論。三、事件累積雨量僅涵蓋臺南與高雄兩站，不足以代表全"
                "臺降水。四、路徑分類為人工判定，邊界個案存在主觀成分。\n\n"
                "以上限制不影響本報告的結論，但決定了這些結論能推論到多遠。"
            ),
        }
    ]
    return sections


def _seed_climatology_report(
    services: Services, catalogue, analysis_dataset, charts: dict[str, str]
) -> None:
    """The meteorological reading of the record, as a standing document."""
    if services.reports.repository.get_by_name(CLIMATOLOGY_REPORT_NAME):
        return
    if not charts:
        return

    sections = _climatology_sections(charts)
    if analysis_dataset and analysis_dataset.current_version_id:
        sections.append(
            {
                "kind": "table",
                "title": "分析資料表（前 15 筆）",
                "dataset_version_id": analysis_dataset.current_version_id,
                "options": {"limit": 15},
            }
        )

    try:
        services.reports.create(
            name=CLIMATOLOGY_REPORT_NAME,
            description=(
                "侵臺颱風的頻率、季節、強度、降水與路徑特徵，以及這些結論的適用範圍。"
            ),
            sections=sections,
            tags=["typhoon", "climatology"],
        )
        logger.info("seeded the typhoon climatology report")
    except FluxError as exc:
        logger.warning("could not seed the climatology report: %s", exc)
