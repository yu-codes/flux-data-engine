"""The analysis pipeline, and the column names it leaves behind.

The pipeline is written here rather than in the seeder because the decision
engine reads what it produces, and two files describing the same table is one
file too many: a step renamed in the seeder and not in the engine fails at run
time, on a fresh install, with a KeyError.

So the step definitions and the names they produce live together, the engine
imports the names, and `test_asset_maintenance.py` runs the pipeline and checks
that the names are actually there.

The chain, in three pipelines, is the doc's data flow made concrete:

    telemetry (600k hourly readings, long)
      → conditioning     drop duplicates, refuse unusable readings, attach
                         operating state, keep only the hours the machine was
                         running, attach the asset and the weather, resample to
                         one row per asset per measurement per day
      → features         attach the response model and compute what each
                         reading *should* have been at this load in this plant
                         room; attach the threshold table and classify; compute
                         trailing spread, slope and z-score
      → snapshot        the latest day per measurement: the fleet as it stands

Every step is a registered transform or a registered provider configured by
parameters. None of it is code written for this domain, which is the property
that makes the same three pipelines re-pointable at any other kind of
equipment.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------
CONDITIONING_PIPELINE = "設備遙測條件化"
DAILY_PIPELINE = "設備每日狀態"
FEATURES_PIPELINE = "設備狀態特徵"
SNAPSHOT_PIPELINE = "設備現況快照"

#  Four pipelines rather than one, each leaving exactly one dataset behind.
#  The split is not cosmetic: the conditioned hourly readings are what the
#  data-quality check has to run on (a check over a stream that mixes running
#  and idle hours reports every idle reading as an outlier), and the daily
#  table is what every chart reads. Both are worth being able to open.
CONDITIONED_READINGS = f"{CONDITIONING_PIPELINE} output"
DAILY_READINGS = f"{DAILY_PIPELINE} output"
DAILY_FEATURES = f"{FEATURES_PIPELINE} output"
CURRENT_SNAPSHOT = f"{SNAPSHOT_PIPELINE} output"

TELEMETRY_DATASET = "設備遙測資料"
OPERATING_DATASET = "設備運轉狀態"
ENVIRONMENT_DATASET = "廠區環境資料"
ASSETS_DATASET = "設備主檔"
SPECIFICATIONS_DATASET = "設備規格"
SENSORS_DATASET = "感測器清單"
MAINTENANCE_DATASET = "維修歷史"
FAILURE_DATASET = "故障歷史"
POLICY_DATASET = "保養政策"
THRESHOLD_DATASET = "狀態門檻"
RESPONSE_DATASET = "設備響應模型"
RULES_DATASET = "工程判斷規則"
TRUTH_DATASET = "模擬真值"

#  Two quality models, because they ask different questions of different
#  streams: one about the readings the analysis uses, one about whether the
#  readings arrived at all.
QUALITY_MODEL = "設備量測資料品質"
QUALITY_DATASET = f"{QUALITY_MODEL} result"
SAMPLING_MODEL = "遙測取樣完整性"
SAMPLING_DATASET = f"{SAMPLING_MODEL} result"

#  Windows the analysis is written against, in days. Named because they appear
#  in four places each and a number typed four times is a number that drifts.
SHORT_WINDOW = 7
LONG_WINDOW = 21
BASELINE_WINDOW = 30

# -- columns the features pipeline produces --------------------------------
#  Identity
C_ASSET = "asset_id"
C_TYPE = "asset_type"
C_SITE = "site_id"
C_CRITICALITY = "criticality"
C_PARAMETER = "parameter"
C_UNIT = "unit"
C_DAY = "day"

#  What was measured
C_SAMPLES = "sample_count"
C_MEAN = "value_mean"
C_MAX = "value_max"
C_MIN = "value_min"
C_SPREAD = "value_std"
C_LOAD = "load_pct_mean"
C_LOAD_PEAK = "load_pct_max"
C_AMBIENT = "ambient_temperature_c_mean"
C_RUNTIME = "runtime_hours_total_max"
C_STARTS = "start_count_total_max"

#  What it should have been, and what it was instead
C_EXPECTED = "expected_value"
C_RESIDUAL = "residual"
C_DEVIATION = "deviation_pct"

#  Where it sits against the threshold table. `oriented_excess` is the daily
#  residual turned so that positive always means "worse", whichever direction
#  this measurement fails in — the one number a single rule set can compare
#  for a bearing temperature and an oil pressure alike.
C_EXCESS = "oriented_excess"
C_LIMIT_PROGRESS = "limit_progress_pct"
C_STATUS = "threshold_status"
C_RANK = "threshold_rank"

#  How it is moving
C_RESIDUAL_MEAN = f"residual_roll_mean{SHORT_WINDOW}"
C_RESIDUAL_SPREAD = f"residual_roll_std{SHORT_WINDOW}"
C_RESIDUAL_SLOPE = f"residual_roll_slope{LONG_WINDOW}"
C_RESIDUAL_Z = f"residual_roll_zscore{LONG_WINDOW}"
C_PROGRESS_SLOPE = f"progress_roll_slope{LONG_WINDOW}"
C_PROGRESS_FIT = f"progress_roll_r_squared{LONG_WINDOW}"
C_BASELINE_SPREAD = f"residual_roll_std{BASELINE_WINDOW}"

#  Every column the engine reads. Asserted against a real run, so a step that
#  stops producing one fails a test rather than a fleet assessment.
REQUIRED_FEATURE_COLUMNS = (
    C_ASSET, C_TYPE, C_SITE, C_CRITICALITY, C_PARAMETER, C_UNIT, C_DAY,
    C_SAMPLES, C_MEAN, C_MAX, C_MIN, C_SPREAD, C_LOAD, C_LOAD_PEAK, C_AMBIENT,
    C_RUNTIME, C_STARTS, C_EXPECTED, C_RESIDUAL, C_DEVIATION, C_EXCESS,
    C_LIMIT_PROGRESS, C_STATUS, C_RANK, C_RESIDUAL_MEAN, C_RESIDUAL_SPREAD,
    C_RESIDUAL_SLOPE, C_RESIDUAL_Z, C_PROGRESS_SLOPE, C_PROGRESS_FIT,
    C_BASELINE_SPREAD,
    "direction", "direction_sign", "warning_value", "critical_value",
    "emergency_value", "reference_value", "parameter_label",
)


# --------------------------------------------------------------------------
# pipeline one: conditioning
# --------------------------------------------------------------------------
def conditioning_steps(datasets: dict[str, str]) -> list[dict[str, Any]]:
    """Raw readings in, trustworthy readings of a running machine out.

    `datasets` maps a name to the id of the reference table wired into the
    joins, because a join's second input is a dataset rather than an earlier
    step: the weather is not derived from the readings.
    """
    steps: list[dict[str, Any]] = [
        {
            "name": "去除重複讀值",
            "provider": "python-transform",
            "description": (
                "同一支感測器同一時刻只該有一個讀值。歷史資料庫重寫是常態，"
                "留著它會讓後面的平均值取決於檔案順序。"
            ),
            "configuration": {
                "transform": "drop_duplicates",
                "options": {"columns": ["timestamp", "asset_id", "parameter"]},
            },
        },
        {
            "name": "移除無讀值紀錄",
            "provider": "python-transform",
            "description": "沒有讀到值的列不是零，是沒有資料。",
            "configuration": {
                "transform": "filter_rows",
                "options": {"column": "quality", "op": "not_equals", "value": "no_data"},
            },
        },
        {
            "name": "移除物理上不可能的讀值",
            "provider": "python-transform",
            "description": (
                "超出量測範圍的讀值是儀器故障，不是設備狀態；"
                "留在平均值裡會把一台正常設備算成異常。"
            ),
            "configuration": {
                "transform": "filter_rows",
                "options": {"column": "quality", "op": "not_equals", "value": "bad"},
            },
        },
        {
            "name": "併入運轉狀態",
            "provider": "join",
            "description": (
                "每一筆讀值配上當時的運轉狀態與負載——這是「90 安培算不算高」"
                "唯一能被回答的前提。"
            ),
            "configuration": {
                "on": ["timestamp", "asset_id"],
                "how": "inner",
                "columns": [
                    "operating_state", "running", "load_pct",
                    "runtime_hours_total", "start_count_total",
                ],
            },
            "input_datasets": {"right": datasets[OPERATING_DATASET]},
        },
        {
            "name": "只保留運轉中的時段",
            "provider": "python-transform",
            "description": (
                "停機時的振動是零、電流是零。把它們算進平均值，會讓一台"
                "週末停機的設備看起來比連續運轉的健康。"
            ),
            "configuration": {
                "transform": "filter_rows",
                "options": {
                    "column": "operating_state",
                    "op": "in",
                    "value": ["running", "overload"],
                },
            },
        },
        {
            "name": "併入設備主檔",
            "provider": "join",
            "description": "設備類型、廠區與重要程度，後續每一層都要用到。",
            "configuration": {
                "on": ["asset_id"],
                "how": "inner",
                "columns": ["asset_type", "site_id", "criticality"],
            },
            "input_datasets": {"right": datasets[ASSETS_DATASET]},
        },
        {
            "name": "併入環境資料",
            "provider": "join",
            "description": (
                "同一台馬達在 25°C 與 40°C 廠房裡的軸承溫度本來就不同；"
                "沒有環境資料，季節就會被讀成劣化。"
            ),
            "configuration": {
                "on": ["timestamp", "site_id"],
                "how": "inner",
                "columns": ["ambient_temperature_c", "relative_humidity_pct",
                            "voltage_thd_pct"],
            },
            "input_datasets": {"right": datasets[ENVIRONMENT_DATASET]},
        },
    ]
    return _chain(steps)


# --------------------------------------------------------------------------
# pipeline two: daily
# --------------------------------------------------------------------------
def daily_steps() -> list[dict[str, Any]]:
    """One row per asset per measurement per day.

    Its own pipeline rather than the tail of the one above, because the hourly
    conditioned readings are a dataset somebody wants: it is what the
    data-quality check runs on and what a telemetry chart draws.
    """
    return _chain(
        [
            {
                "name": "彙整為每日狀態",
                "provider": "python-transform",
                "description": (
                    "每台設備每個量測每天一列：平均、峰值、最低、離散度，"
                    "以及當天的平均負載與環境溫度。取樣不規則的兩條序列要能比較，"
                    "必須先放到同一個時間網格上。"
                ),
                "configuration": {
                    "transform": "resample_time",
                    "options": {
                        "timestamp": "timestamp",
                        "period": "day",
                        "output": C_DAY,
                        "group_by": [
                            C_ASSET, C_TYPE, C_SITE, C_CRITICALITY, C_PARAMETER, C_UNIT,
                        ],
                        "measures": {
                            "value": ["mean", "max", "min", "std"],
                            "load_pct": ["mean", "max"],
                            "ambient_temperature_c": ["mean"],
                            "runtime_hours_total": ["max"],
                            "start_count_total": ["max"],
                        },
                    },
                },
            }
        ]
    )


# --------------------------------------------------------------------------
# pipeline three: features
# --------------------------------------------------------------------------
#  What the physics says the reading should have been, and what it was
#  instead. `deviation_pct` is oriented: positive always means "worse", in
#  whichever direction this measurement fails, so one rule serves a bearing
#  temperature and an oil pressure without being written twice.
_EXPECTED = (
    "intercept + load_coefficient * (load_pct_mean / 100.0)"
    " + ambient_coefficient * ambient_temperature_c_mean"
)

#  How far this measurement has travelled from where it should be towards the
#  emergency limit, as a percentage: 0 when the reading is exactly what the
#  response model predicts, 100 at the emergency offset, negative when the
#  asset is running better than predicted. It is the one number that compares
#  a vibration in mm/s against a dissolved gas in ppm.
_PROGRESS = "100.0 * oriented_excess / (oriented_emergency + 0.000001)"


def feature_steps(datasets: dict[str, str]) -> list[dict[str, Any]]:
    """Daily readings in, the evidence an assessment is built from out."""
    steps: list[dict[str, Any]] = [
        {
            "name": "併入響應模型",
            "provider": "join",
            "description": (
                "每個設備類型每個量測對負載與環境的響應係數。這是工程知識，"
                "以資料表的形式帶進來，而不是寫死在程式裡。"
            ),
            "configuration": {
                "on": [C_TYPE, C_PARAMETER],
                "how": "left",
                #  Narrowed on purpose. Both reference tables carry `unit`,
                #  `direction` and a label, so taking them whole produced two
                #  columns called `unit_right` and Arrow refused the table.
                "columns": ["parameter_label", "intercept", "load_coefficient",
                            "ambient_coefficient", "direction", "direction_sign"],
            },
            "input_datasets": {"right": datasets[RESPONSE_DATASET]},
        },
        {
            "name": "計算應有值與偏差",
            "provider": "formula",
            "description": (
                "預期值 = 截距 + 負載係數×負載 + 環境係數×環境溫度。"
                "實測減預期，就把負載與季節都扣掉了，剩下的才是設備本身。"
            ),
            "configuration": {
                "expressions": {
                    C_EXPECTED: _EXPECTED,
                    C_RESIDUAL: f"{C_MEAN} - {C_EXPECTED}",
                    #  Oriented: positive is always the bad direction.
                    C_DEVIATION: (
                        f"direction_sign * 100.0 * {C_RESIDUAL}"
                        f" / (abs({C_EXPECTED}) + 0.000001)"
                    ),
                },
                "keep_input_columns": True,
            },
        },
        {
            "name": "併入狀態門檻",
            "provider": "join",
            "description": (
                "各類設備各量測的警戒／嚴重／緊急界線。界線是「相對於應有值的"
                "偏移量」而不是固定值——固定的流量下限會把一台 45% 負載的正常泵"
                "永遠判成異常。"
            ),
            "configuration": {
                "on": [C_TYPE, C_PARAMETER],
                "how": "left",
                "columns": ["warning_offset", "critical_offset", "emergency_offset",
                            "reference_value"],
            },
            "input_datasets": {"right": datasets[THRESHOLD_DATASET]},
        },
        {
            "name": "換算門檻進度",
            "provider": "formula",
            "description": (
                "把「往壞的方向」統一成正值，換算成 0（如預期）到 100（緊急界線）"
                "的進度，並把界線加回應有值，得到當下這個工況真正的絕對界線。"
            ),
            "configuration": {
                "expressions": {
                    C_EXCESS: f"direction_sign * {C_RESIDUAL}",
                    "oriented_warning": "direction_sign * warning_offset",
                    "oriented_critical": "direction_sign * critical_offset",
                    "oriented_emergency": "direction_sign * emergency_offset",
                    C_LIMIT_PROGRESS: _PROGRESS,
                    #  The number an operator reads: the limit as it stands at
                    #  today's load and today's plant temperature.
                    "warning_value": f"{C_EXPECTED} + warning_offset",
                    "critical_value": f"{C_EXPECTED} + critical_offset",
                    "emergency_value": f"{C_EXPECTED} + emergency_offset",
                },
                "keep_input_columns": True,
            },
        },
        {
            "name": "判定門檻狀態",
            "provider": "rule",
            "description": (
                "四級門檻分類。規則是資料而不是程式：改門檻政策不需要改程式，"
                "而且系統不會只依賴統計或 AI。"
            ),
            "configuration": {
                "rules": [
                    {
                        "name": "emergency",
                        "when": f"{C_EXCESS} >= oriented_emergency",
                        "then": {C_STATUS: "emergency", C_RANK: 3},
                    },
                    {
                        "name": "critical",
                        "when": f"{C_EXCESS} >= oriented_critical",
                        "then": {C_STATUS: "critical", C_RANK: 2},
                    },
                    {
                        "name": "warning",
                        "when": f"{C_EXCESS} >= oriented_warning",
                        "then": {C_STATUS: "warning", C_RANK: 1},
                    },
                ],
                "default": {C_STATUS: "normal", C_RANK: 0},
                "mode": "first_match",
            },
        },
        {
            "name": f"{SHORT_WINDOW} 日滾動統計",
            "provider": "python-transform",
            "description": "近期水準與離散度：劣化中的設備不只偏高，也更不穩定。",
            "configuration": {
                "transform": "rolling_stats",
                "options": {
                    "column": C_RESIDUAL,
                    "window": SHORT_WINDOW,
                    "statistics": ["mean", "std"],
                    "group_by": [C_ASSET, C_PARAMETER],
                    "order_by": C_DAY,
                    "min_periods": 3,
                    "prefix": "residual_roll",
                },
            },
        },
        {
            "name": f"{LONG_WINDOW} 日趨勢",
            "provider": "python-transform",
            "description": (
                "偏差的斜率與 z 分數。尚未越線但持續上升的量測，"
                "是門檻永遠看不到而條件式維護唯一存在的理由。"
            ),
            "configuration": {
                "transform": "rolling_stats",
                "options": {
                    "column": C_RESIDUAL,
                    "window": LONG_WINDOW,
                    "statistics": ["slope", "zscore"],
                    "group_by": [C_ASSET, C_PARAMETER],
                    "order_by": C_DAY,
                    "per": "day",
                    "min_periods": 6,
                    "prefix": "residual_roll",
                },
            },
        },
        {
            "name": "門檻進度趨勢",
            "provider": "python-transform",
            "description": (
                "每天往緊急界線前進幾個百分點，以及那條直線有多可信——"
                "維修窗口就是從這兩個數字推的。"
            ),
            "configuration": {
                "transform": "rolling_stats",
                "options": {
                    "column": C_LIMIT_PROGRESS,
                    "window": LONG_WINDOW,
                    "statistics": ["slope", "r_squared"],
                    "group_by": [C_ASSET, C_PARAMETER],
                    "order_by": C_DAY,
                    "per": "day",
                    "min_periods": 6,
                    "prefix": "progress_roll",
                },
            },
        },
        {
            "name": f"{BASELINE_WINDOW} 日基線離散度",
            "provider": "python-transform",
            "description": "設備自身的正常波動幅度，用來判斷現在的偏差算不算大。",
            "configuration": {
                "transform": "rolling_stats",
                "options": {
                    "column": C_RESIDUAL,
                    "window": BASELINE_WINDOW,
                    "statistics": ["std"],
                    "group_by": [C_ASSET, C_PARAMETER],
                    "order_by": C_DAY,
                    "min_periods": 10,
                    "prefix": "residual_roll",
                },
            },
        },
    ]
    return _chain(steps)


# --------------------------------------------------------------------------
# pipeline four: the snapshot
# --------------------------------------------------------------------------
def snapshot_steps() -> list[dict[str, Any]]:
    """The latest day per measurement: the fleet as it stands right now."""
    return _chain(
        [
            {
                "name": "由新到舊排序",
                "provider": "python-transform",
                "description": "最新的一天要排在最前面，下一步才留得住它。",
                "configuration": {
                    "transform": "sort_rows",
                    "options": {"column": C_DAY, "descending": True},
                },
            },
            {
                "name": "每個量測留下最新一天",
                "provider": "python-transform",
                "description": (
                    "去重保留第一列，而第一列現在是最新的一天——"
                    "所以這一步得到的是全機隊的現況。"
                ),
                "configuration": {
                    "transform": "drop_duplicates",
                    "options": {"columns": [C_ASSET, C_PARAMETER]},
                },
            },
        ]
    )


def _chain(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wire each step to the one before it.

    Leaving `input_from` unset means "the pipeline's input dataset", which
    turns a chain into a fan of branches that each ignore the others and still
    succeeds — with the wrong answer.
    """
    previous: str | None = None
    for step in steps:
        step["input_from"] = previous
        previous = step["name"]
    return steps
