"""The typhoon climatology: raw catalogue in, meteorological analysis out.

This is the data-analysis half of the typhoon example. It exists to show what
the platform looks like when a real, messy record is treated properly:

    catalogue (440 rows, text where numbers belong)
        -> a twelve-step Pipeline built only from standard transforms
        -> an analysis table with intensity, season, lifetime and rainfall bands
        -> fourteen charts covering frequency, intensity, rainfall and track
        -> three dashboards, one per question a forecaster actually asks

Every step is a registered transform configured by parameters — no step needs
code written for it, which is the point: the same twelve verbs reshape any
source, and a different analysis is a different arrangement of them.

Meteorological conventions used here follow the CWA (中央氣象署):

  * Intensity by maximum near-centre wind: tropical depression below 17.2 m/s,
    mild 17.2-32.6, moderate 32.7-50.9, severe 51.0 and above.
  * Rainfall bands by event total: 大雨 80 mm, 豪雨 200 mm, 大豪雨 350 mm,
    超大豪雨 500 mm.
  * Landfall-track classes 1-9 are the CWA's own 侵臺路徑分類.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.container import Services
from app.shared.errors import FluxError

logger = logging.getLogger(__name__)

PIPELINE_NAME = "Typhoon climatology"
ANALYSIS_STEP = "label track category"

#  CWA 侵臺路徑分類, shortened to what fits on a chart axis. The full sentence
#  lives in the algorithms package and is shown in the typhoon application.
TRACK_CATEGORY_SHORT = {
    "1": "1 北部海面西行",
    "2": "2 通過北部",
    "3": "3 通過中部",
    "4": "4 通過南部",
    "5": "5 南部海面西行",
    "6": "6 東岸北上",
    "7": "7 南部海面東行",
    "8": "8 南部海面北上",
    "9": "9 未侵襲但有影響",
    "特殊": "特殊路徑",
}

#  Class order as the CWA lists it, so an axis never reads alphabetically.
TRACK_ORDER = list(TRACK_CATEGORY_SHORT.values())

INTENSITY_EDGES = [0, 17.2, 32.7, 51.0, 120.0]
INTENSITY_LABELS = ["熱帶性低氣壓", "輕度颱風", "中度颱風", "強烈颱風"]

RAINFALL_EDGES = [0, 80, 200, 350, 500, 3000]
RAINFALL_LABELS = ["未達大雨", "大雨", "豪雨", "大豪雨", "超大豪雨"]

DECADE_EDGES = [1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020, 2030]
DECADE_LABELS = ["1950s", "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"]

#  Columns that are links, filenames or free prose: useful in the catalogue,
#  noise in an analysis table.
NARRATIVE_COLUMNS = [
    "cwa_typhoon_detail_url",
    "track_image_filename",
    "track_image_path",
    "track_image_download_url",
    "rainfall_summary_filename",
    "rainfall_summary_path",
    "rainfall_summary_download_url",
    "station_total_rainfall_chart_filename",
    "station_total_rainfall_chart_path",
    "station_total_rainfall_chart_download_url",
    "ibtracs_detail_url",
    "ibtracs_match_strategy",
    "ibtracs_unmatched_reason",
    "path",
    "path_filename",
    "path_source",
]


# --------------------------------------------------------------------------
# the pipeline, expressed as twelve standard transforms
# --------------------------------------------------------------------------
def _steps() -> list[dict[str, Any]]:
    """One dict per step: a transform key and the parameters that shape it."""
    return [
        {
            "name": "cast measures",
            "transform": "cast_types",
            "description": "Numbers arriving as text become numbers, once, here.",
            "options": {
                "casts": {
                    "min_pressure": "number",
                    "max_gust_speed": "number",
                    "max_intensity_value": "number",
                    "seven_level_wind_radius": "number",
                    "ten_level_wind_radius": "number",
                    "event_rain_tn": "number",
                    "event_rain_kh": "number",
                    "warning_count": "integer",
                    "taiwan_impact_count": "integer",
                    "path_point_count": "integer",
                    "year": "integer",
                }
            },
        },
        {
            "name": "parse near-centre wind",
            "transform": "parse_numeric",
            "description": "Lifts the number out of '30 (公尺/秒)' into wind_ms.",
            "options": {
                "column": "max_wind_near_center",
                "output": "wind_ms",
                "keep_original": True,
            },
        },
        {
            "name": "derive season",
            "transform": "datetime_parts",
            "description": "Genesis month and day-of-year, so seasonality is askable.",
            "options": {
                "column": "genesis_time",
                "prefix": "genesis",
                "parts": ["month", "dayofyear"],
            },
        },
        {
            "name": "derive lifetime",
            "transform": "duration_between",
            "description": "Hours from genesis to dissipation.",
            "options": {
                "start": "genesis_time",
                "end": "dissipation_time",
                "unit": "hours",
                "output": "lifetime_hours",
            },
        },
        {
            "name": "derive warning duration",
            "transform": "duration_between",
            "description": "How long the sea warning stood — the exposure window.",
            "options": {
                "start": "sea_warning_issue_time",
                "end": "sea_warning_lift_time",
                "unit": "hours",
                "output": "sea_warning_hours",
            },
        },
        {
            "name": "flag landfall",
            "transform": "flag_rows",
            "description": "True when the CWA recorded a landfall location.",
            "options": {
                "column": "landfall_location",
                "op": "not_empty",
                "output": "made_landfall",
            },
        },
        {
            "name": "band intensity",
            "transform": "bin_numeric",
            "description": "CWA intensity classes from the near-centre wind.",
            "options": {
                "column": "wind_ms",
                "edges": INTENSITY_EDGES,
                "labels": INTENSITY_LABELS,
                "output": "intensity_band",
            },
        },
        {
            "name": "band rainfall",
            "transform": "bin_numeric",
            "description": "CWA rainfall classes from the Tainan event total.",
            "options": {
                "column": "event_rain_tn",
                "edges": RAINFALL_EDGES,
                "labels": RAINFALL_LABELS,
                "output": "rain_band_tainan",
            },
        },
        {
            "name": "band decade",
            "transform": "bin_numeric",
            "description": "Decade of occurrence, for trend comparisons.",
            "options": {
                "column": "year",
                "edges": DECADE_EDGES,
                "labels": DECADE_LABELS,
                "output": "decade",
            },
        },
        {
            "name": "keep classified",
            "transform": "filter_rows",
            "description": (
                "Only typhoons the CWA assigned a landfall-track class: the "
                "population the analog model is validated against."
            ),
            "options": {"column": "taiwan_track_category", "op": "not_empty"},
        },
        {
            "name": "label track category",
            "transform": "map_values",
            "description": "Turns the class code into something readable on an axis.",
            "options": {
                "column": "taiwan_track_category",
                "mapping": TRACK_CATEGORY_SHORT,
                "output": "track_category",
            },
        },
        {
            "name": "trim narrative columns",
            "transform": "drop_columns",
            "description": "Drops links, filenames and prose the charts never read.",
            "options": {"columns": NARRATIVE_COLUMNS},
        },
    ]


def _step_model_name(step: dict[str, Any]) -> str:
    return f"Typhoon · {step['name']}"



def seed_climatology_pipeline(services: Services, catalogue):
    """Chain the steps, run once, and hand back the analysis dataset.

    Each step carries its own transform and options. This used to create a
    ModelDefinition per step - twelve models, twelve versions, all of them
    immediately hidden from the library because none of them was a model
    anybody would go looking for.
    """
    chained = []
    previous: str | None = None
    for step in _steps():
        chained.append(
            {
                "name": step["name"],
                "provider": "python-transform",
                "configuration": {
                    "transform": step["transform"],
                    "options": step["options"],
                },
                "input_from": previous,
                "description": step["description"],
                #  The charts read this step, not the end of the chain: the
                #  trim step after it exists to show the graph continues past
                #  the analysis. Keeping a mid-chain output is what the
                #  `materialise` override is for.
                "materialise": step["name"] == ANALYSIS_STEP,
            }
        )
        previous = step["name"]

    wanted = [step["name"] for step in chained]
    pipeline = services.pipelines.repository.get_by_name(PIPELINE_NAME)
    if pipeline and [s.name for s in pipeline.steps] != wanted:
        #  An install seeded by an earlier version carries the older, shorter
        #  chain. Bring it up to date rather than leaving two half-pipelines.
        try:
            pipeline = services.pipelines.update(pipeline.id, {"steps": chained})
            logger.info("upgraded the climatology pipeline to %s steps", len(chained))
        except FluxError as exc:
            logger.warning("could not upgrade the climatology pipeline: %s", exc)
            return None
    if not pipeline:
        try:
            pipeline = services.pipelines.create(
                name=PIPELINE_NAME,
                input_dataset_id=catalogue.id,
                description=(
                    "Twelve standard transforms turn the raw CWA catalogue into "
                    "an analysis table: types cast, wind parsed, season and "
                    "lifetime derived, intensity and rainfall banded, and the "
                    "track class labelled."
                ),
                tags=["typhoon", "climatology"],
                steps=chained,
            )
        except FluxError as exc:
            logger.warning("could not seed the climatology pipeline: %s", exc)
            return None

    #  A run from an older chain is not a run of this pipeline: comparing the
    #  step names is what makes re-seeding after an upgrade actually re-run.
    runs = services.pipelines.list_runs(pipeline_id=pipeline.id, limit=1)
    run = runs[0] if runs else None
    if run and [s.step_name for s in run.step_runs] != wanted:
        run = None
    #  A run that did not leave behind the table the charts read is not a run
    #  this seed can reuse, whatever its step names say. Checking the names
    #  alone missed the case where a step stopped materialising its output.
    if run and not any(
        s.step_name == ANALYSIS_STEP and s.dataset_id for s in run.step_runs
    ):
        run = None
    if not run or run.status.value != "succeeded":
        try:
            run = services.pipelines.run(pipeline.id)
        except FluxError as exc:
            logger.warning("the climatology pipeline could not be run: %s", exc)
            return None

    if run.status.value != "succeeded":
        logger.warning("climatology pipeline finished %s: %s", run.status.value, run.error)
        return None

    #  The labelled table, not the trimmed one: charts want every measure, and
    #  the trim step exists to show that the graph continues past the analysis.
    final = next((s for s in run.step_runs if s.step_name == ANALYSIS_STEP), None)
    if not final or not final.dataset_id:
        logger.warning(
            "climatology run has no dataset for step '%s'; charts skipped", ANALYSIS_STEP
        )
        return None
    logger.info("climatology pipeline produced %s analysis rows", final.row_count)
    return services.datasets.get(final.dataset_id)


# --------------------------------------------------------------------------
# the charts
# --------------------------------------------------------------------------
def _charts() -> list[dict[str, Any]]:
    """Fourteen readings of the record, grouped by the question they answer."""
    return [
        # ---------------------------------------------- frequency & season
        {
            "group": "frequency",
            "name": "每年侵臺颱風個數",
            "description": "Annual count of typhoons the CWA assigned a track class",
            "spec": {
                "chart_type": "line",
                "x": "year",
                "y": ["wind_ms"],
                "aggregation": "count",
                "x_title": "年份",
                "y_title": "颱風個數",
                "unit": "個",
                "subtitle": "一年一點，涵蓋紀錄的第一年到最後一年",
            },
        },
        {
            "group": "frequency",
            "name": "生成月份分布",
            "description": "Seasonality: which months produce the typhoons reaching Taiwan",
            "spec": {
                "chart_type": "bar",
                "x": "genesis_month",
                "y": ["wind_ms"],
                "aggregation": "count",
                "x_title": "生成月份",
                "y_title": "颱風個數",
                "unit": "個",
                "subtitle": "以生成時間的月份計；侵臺季節集中在 7-9 月",
                "value_labels": True,
            },
        },
        {
            "group": "frequency",
            "name": "各年代強度組成",
            "description": "Intensity mix per decade — is the record getting stronger?",
            "spec": {
                "chart_type": "stacked_bar",
                "x": "decade",
                "series": "intensity_band",
                "series_order": INTENSITY_LABELS,
                "y": ["wind_ms"],
                "aggregation": "count",
                "x_title": "年代",
                "y_title": "颱風個數",
                "unit": "個",
                "subtitle": "堆疊高度是該年代的總數，分段是強度組成",
            },
        },
        {
            "group": "frequency",
            "name": "路徑分類佔比",
            "description": "Share of each CWA landfall-track class",
            "spec": {
                "chart_type": "pie",
                "x": "track_category",
                "x_order": TRACK_ORDER,
                "y": ["wind_ms"],
                "aggregation": "count",
                "x_title": "侵臺路徑分類",
                "y_title": "颱風個數",
                "unit": "個",
                "subtitle": "九類侵臺路徑加上特殊路徑的整體佔比",
            },
        },
        # ------------------------------------------------------- intensity
        {
            "group": "intensity",
            "name": "近中心最大風速分布",
            "description": "Distribution of maximum near-centre wind speed",
            "spec": {
                "chart_type": "histogram",
                "y": ["wind_ms"],
                "bins": 16,
                "x_title": "近中心最大風速",
                "y_title": "颱風個數",
                "unit": "m/s",
                "subtitle": "等寬分組；分布形狀比平均值更能說明強度結構",
            },
        },
        {
            "group": "intensity",
            "name": "中心最低氣壓分布",
            "description": "Distribution of minimum central pressure",
            "spec": {
                "chart_type": "histogram",
                "y": ["min_pressure"],
                "bins": 14,
                "x_title": "中心最低氣壓",
                "y_title": "颱風個數",
                "unit": "hPa",
                "subtitle": "僅計入有氣壓紀錄的個案；早期紀錄多為空值",
            },
        },
        {
            "group": "intensity",
            "name": "風速–氣壓關係",
            "description": "The classic wind-pressure relationship, one point per typhoon",
            "spec": {
                "chart_type": "scatter",
                "x": "min_pressure",
                "y": ["wind_ms"],
                "sort_by": "min_pressure",
                "x_title": "中心最低氣壓 (hPa)",
                "y_title": "近中心最大風速",
                "unit": "m/s",
                "subtitle": "氣壓越低、風速越高：熱帶氣旋最穩定的經驗關係",
                "limit": 500,
            },
        },
        {
            "group": "intensity",
            "name": "各路徑分類的風速分布",
            "description": "Wind speed spread within each track class",
            "spec": {
                "chart_type": "box",
                "x": "track_category",
                "y": ["wind_ms"],
                "x_order": TRACK_ORDER,
                "x_title": "侵臺路徑分類",
                "y_title": "近中心最大風速",
                "unit": "m/s",
                "subtitle": "盒為四分位距，鬚線至 1.5 IQR 內的極值",
            },
        },
        {
            "group": "intensity",
            "name": "生命期時數分布",
            "description": "How long typhoons last from genesis to dissipation",
            "spec": {
                "chart_type": "histogram",
                "y": ["lifetime_hours"],
                "bins": 14,
                "x_title": "生命期",
                "y_title": "颱風個數",
                "unit": "小時",
                "subtitle": "自生成到消散；長生命期通常伴隨較高強度",
            },
        },
        # -------------------------------------------------------- rainfall
        {
            "group": "rainfall",
            "name": "各強度的臺南事件雨量分布",
            "description": "Tainan event rainfall spread, by intensity class",
            "spec": {
                "chart_type": "box",
                "x": "intensity_band",
                "y": ["event_rain_tn"],
                "x_order": INTENSITY_LABELS,
                "x_title": "強度分類",
                "y_title": "臺南事件累積雨量",
                "unit": "mm",
                "subtitle": "雨量分布右偏，中位數與四分位距比平均值可靠",
            },
        },
        {
            "group": "rainfall",
            "name": "各路徑分類平均雨量（臺南 / 高雄）",
            "description": "Mean event rainfall for two southern stations, per track class",
            "spec": {
                "chart_type": "bar",
                "x": "track_category",
                "x_order": TRACK_ORDER,
                "y": ["event_rain_tn", "event_rain_kh"],
                "aggregation": "mean",
                "x_title": "侵臺路徑分類",
                "y_title": "平均事件累積雨量",
                "unit": "mm",
                "subtitle": "兩站並列，看得出哪些路徑對西南部特別致雨",
            },
        },
        {
            "group": "rainfall",
            "name": "月份 × 路徑分類熱區圖",
            "description": "When each track class occurs, as a month-by-class grid",
            "spec": {
                "chart_type": "heatmap",
                "x": "genesis_month",
                "series": "track_category",
                "series_order": TRACK_ORDER,
                "y": ["wind_ms"],
                "aggregation": "count",
                "x_title": "生成月份",
                "y_title": "颱風個數",
                "unit": "個",
                "subtitle": "格子越深代表該月該類越常出現",
            },
        },
        {
            "group": "rainfall",
            "name": "雨量分級組成（依強度）",
            "description": "Rainfall-class composition within each intensity class",
            "spec": {
                "chart_type": "stacked_bar",
                "x": "intensity_band",
                "x_order": INTENSITY_LABELS,
                "series": "rain_band_tainan",
                "series_order": RAINFALL_LABELS,
                "y": ["event_rain_tn"],
                "aggregation": "count",
                "x_title": "強度分類",
                "y_title": "颱風個數",
                "unit": "個",
                "subtitle": "以中央氣象署雨量分級（大雨 / 豪雨 / 大豪雨 / 超大豪雨）",
            },
        },
        # ----------------------------------------------------------- track
        {
            "group": "track",
            "name": "生成位置分布",
            "description": "Genesis longitude against latitude, one point per typhoon",
            "spec": {
                "chart_type": "scatter",
                "x": "genesis_longitude",
                "y": ["genesis_latitude"],
                "sort_by": "genesis_longitude",
                "x_title": "生成經度 (°E)",
                "y_title": "生成緯度",
                "unit": "°N",
                "subtitle": "西北太平洋主要生成區，越靠西越接近臺灣",
                "limit": 500,
            },
        },
        {
            "group": "track",
            "name": "各分類登陸比例",
            "description": "Share of each track class that made landfall on Taiwan",
            "spec": {
                "chart_type": "bar",
                "x": "track_category",
                "x_order": TRACK_ORDER,
                "y": ["made_landfall"],
                "aggregation": "mean",
                "x_title": "侵臺路徑分類",
                "y_title": "登陸比例",
                "subtitle": "1.0 代表該類全數登陸；穿越本島的類別明顯較高",
                "value_labels": True,
            },
        },
        {
            "group": "track",
            "name": "海上警報時數 vs 風速",
            "description": "Warning duration against intensity",
            "spec": {
                "chart_type": "scatter",
                "x": "wind_ms",
                "y": ["sea_warning_hours"],
                "sort_by": "wind_ms",
                "x_title": "近中心最大風速 (m/s)",
                "y_title": "海上警報時數",
                "unit": "小時",
                "subtitle": "警報時間是暴露窗口，隨強度拉長但離散度很大",
                "limit": 500,
            },
        },
    ]


DASHBOARDS = [
    {
        "name": "颱風氣候概況",
        "group": "frequency",
        "description": (
            "多久來一次、什麼季節來、來的是哪一類：侵臺颱風的頻率與季節結構。"
        ),
    },
    {
        "name": "強度結構分析",
        "group": "intensity",
        "description": (
            "風速、氣壓與生命期的分布，以及各路徑分類之間的強度差異。"
        ),
    },
    {
        "name": "降水與路徑影響",
        "group": "rainfall",
        "description": (
            "哪些路徑、哪個強度會為西南部帶來致災雨量，以中央氣象署雨量分級呈現。"
        ),
    },
    {
        "name": "路徑與登陸特徵",
        "group": "track",
        "description": "生成位置、登陸比例與警報時數，描述侵臺颱風的空間與影響特徵。",
    },
]


def seed_climatology_charts(services: Services, dataset) -> dict[str, str]:
    """Create every chart, then one dashboard per question group."""
    version_id = dataset.current_version_id
    if not version_id:
        return {}

    existing = {viz.name: viz.id for viz in services.visualizations.list()}
    by_group: dict[str, list[str]] = {}
    created: dict[str, str] = {}

    for chart in _charts():
        name = chart["name"]
        viz_id = existing.get(name)
        if viz_id is None:
            try:
                viz_id = services.visualizations.create(
                    name=name,
                    description=chart["description"],
                    dataset_version_id=version_id,
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
