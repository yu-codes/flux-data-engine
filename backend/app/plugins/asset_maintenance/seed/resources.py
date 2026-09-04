"""What the maintenance application declares, as data.

Sources, datasets and the models that are pure configuration. Everything that
is genuinely an action — running the pipelines, computing the charts, scoring
the policies, writing the reports — stays as code in `worked_example.py`,
because those are actions with outcomes and pretending they are data would
mean inventing a language to describe them in.
"""

from __future__ import annotations

from app.plugins.fixtures import Fixture

from .. import features as F
from ..datagen import FILES, ensure_fleet
from ..engine import DEFAULT_POLICY
from ..paths import PROJECT, relative
from .worked_example import (
    APPLICATION,
    DASHBOARDS,
    DECISION_MODEL,
    EVIDENCE_MODEL,
    HEALTH_SCORECARD_MODEL,
    PROJECTION_MODEL,
    REASONING_MODEL,
    RISK_MATRIX_MODEL,
)

#  Names come from the code seeder rather than being retyped here: a name
#  typed twice is a name that drifts, and a dashboard reference that matches
#  nothing fails silently, leaving an application published and empty.


def _source(name: str, file_key: str, kind: str, description: str) -> dict:
    return {
        "name": name,
        "source_type": kind,
        "connection": {"path": relative(FILES[file_key])},
        "description": description,
    }


def fixture() -> Fixture:
    """The resources this application ships with.

    Generating the fleet happens here because the fixture is the first thing
    that runs, and the sources below name files that have to exist by the time
    the datasets are created. See `datagen.ensure_fleet`.
    """
    ensure_fleet()
    return Fixture(
        source="asset_maintenance",
        #  The piece of work all of this belongs to. The core does not know
        #  this name — a general platform must not — so the application that
        #  is this work declares it, and its directory is the one the plugin
        #  already ships its files under.
        project={
            "name": PROJECT,
            "directory": PROJECT,
            "description": (
                "設備預防性維護分析：四十台設備的遙測、運轉狀態、維修歷史，"
                "以及以門檻、統計、趨勢與工程規則合成的維護決策。"
            ),
        },
        sources=[
            _source("設備遙測（Parquet）", "telemetry", "parquet",
                    "每小時一筆的感測器讀值，含品質旗標"),
            _source("設備運轉狀態（Parquet）", "operating", "parquet",
                    "每小時的運轉狀態、負載與累積運轉時數"),
            _source("廠區環境（CSV）", "environment", "csv",
                    "各廠區每小時的環境溫濕度、降雨、粉塵與電力品質"),
            _source("設備主檔（JSON）", "assets", "json",
                    "設備身分、位置、重要程度與設計壽命"),
            _source("設備規格（JSON）", "specifications", "json",
                    "各設備類型不同的銘牌規格，以長格式儲存"),
            _source("感測器清單（JSON）", "sensors", "json",
                    "每台設備配置了哪些量測點與取樣頻率"),
            _source("維修歷史（JSON）", "maintenance", "json",
                    "預防保養、定期檢查與矯正維修紀錄"),
            _source("故障歷史（JSON）", "failures", "json",
                    "故障事件：徵候、根本原因、停機時數與影響"),
            _source("保養政策（JSON）", "policies", "json",
                    "各類設備的時數週期與日曆週期保養政策"),
            _source("狀態門檻（JSON）", "thresholds", "json",
                    "各量測相對於應有值的警戒／嚴重／緊急偏移量"),
            _source("設備響應模型（JSON）", "response", "json",
                    "各量測對負載與環境溫度的響應係數——工程知識，不是程式"),
            _source("工程判斷規則（JSON）", "rules", "json",
                    "以運算式表達的證據組合規則"),
            _source("模擬真值（JSON）", "truth", "json",
                    "本機隊為模擬產生，此表記錄實際發生的劣化與故障，用於回測"),
        ],
        datasets=[
            {
                "name": F.TELEMETRY_DATASET,
                "source": "設備遙測（Parquet）",
                "description": "60 萬筆感測器讀值，120 天、每小時、40 台設備",
                "tags": ["maintenance", "telemetry"],
            },
            {
                "name": F.OPERATING_DATASET,
                "source": "設備運轉狀態（Parquet）",
                "description": "設備當時在做什麼、負載多少——判讀讀值的前提",
                "tags": ["maintenance", "context"],
            },
            {
                "name": F.ENVIRONMENT_DATASET,
                "source": "廠區環境（CSV）",
                "description": "設備所處的環境條件，避免把季節讀成劣化",
                "tags": ["maintenance", "environment"],
            },
            {
                "name": F.ASSETS_DATASET,
                "source": "設備主檔（JSON）",
                "description": "40 台設備，八種類型，三個廠區",
                "tags": ["maintenance", "asset"],
            },
            {
                "name": F.SPECIFICATIONS_DATASET,
                "source": "設備規格（JSON）",
                "description": "以長格式儲存，因此不同類型的設備可以有不同規格欄位",
                "tags": ["maintenance", "asset"],
            },
            {
                "name": F.SENSORS_DATASET,
                "source": "感測器清單（JSON）",
                "description": "222 個量測點",
                "tags": ["maintenance", "asset"],
            },
            {
                "name": F.MAINTENANCE_DATASET,
                "source": "維修歷史（JSON）",
                "description": "徵候 → 原因 → 措施 → 結果，每一次維護都完整記錄",
                "tags": ["maintenance", "history"],
            },
            {
                "name": F.FAILURE_DATASET,
                "source": "故障歷史（JSON）",
                "description": "故障事件與維修事件分開記錄，因為它們不是同一件事",
                "tags": ["maintenance", "history"],
            },
            {
                "name": F.POLICY_DATASET,
                "source": "保養政策（JSON）",
                "description": "傳統預防性保養的基準：時數週期與日曆週期",
                "tags": ["maintenance", "policy"],
            },
            {
                "name": F.THRESHOLD_DATASET,
                "source": "狀態門檻（JSON）",
                "description": "界線是相對於應有值的偏移量，因此在任何負載下都成立",
                "tags": ["maintenance", "policy"],
            },
            {
                "name": F.RESPONSE_DATASET,
                "source": "設備響應模型（JSON）",
                "description": "溫度 ≈ 環境 + 負載效應：物理知識以資料表的形式進入分析",
                "tags": ["maintenance", "policy"],
            },
            {
                "name": F.RULES_DATASET,
                "source": "工程判斷規則（JSON）",
                "description": "16 條證據組合規則，改規則不需要改程式",
                "tags": ["maintenance", "policy"],
            },
            {
                "name": F.TRUTH_DATASET,
                "source": "模擬真值（JSON）",
                "description": "模擬機隊的實際狀態，唯一用途是讓決策政策可以被評分",
                "tags": ["maintenance", "validation"],
            },
        ],
        models=[
            {
                "name": F.QUALITY_MODEL,
                "provider": "data-quality",
                "description": (
                    "對分析真正會用到的讀值（運轉中的時段）做品質評分："
                    "缺漏、離群、卡死、位準跳動與漂移。"
                ),
                "configuration": {
                    "value": "value",
                    "timestamp": "timestamp",
                    "group_by": ["asset_id", "parameter"],
                    #  No gap check here: this stream has been filtered to the
                    #  hours the machine was running, so every night is a gap
                    #  and none of them is a fault.
                    "checks": ["missing", "duplicates", "outliers", "flatline",
                               "step", "drift"],
                    "flatline_readings": 6,
                    "step_ratio": 12.0,
                    "outlier_factor": 3.0,
                    "min_score": 55,
                },
                "tags": ["maintenance", "quality"],
            },
            {
                "name": F.SAMPLING_MODEL,
                "provider": "data-quality",
                "description": (
                    "對原始遙測串流檢查取樣完整性：讀值有沒有到、有沒有重複、"
                    "有沒有超過宣告的取樣間隔。"
                ),
                "configuration": {
                    "value": "value",
                    "timestamp": "timestamp",
                    "group_by": ["asset_id", "parameter"],
                    "checks": ["missing", "duplicates", "gaps"],
                    "expected_interval_minutes": 60,
                    "gap_tolerance": 2.0,
                },
                "tags": ["maintenance", "quality"],
            },
            {
                "name": DECISION_MODEL,
                "provider": "asset-condition-decision",
                "description": (
                    "十個分析器合成一份維護決策：是否需要處置、健康分數、"
                    "風險等級、建議措施、維修窗口與信心度。"
                ),
                "configuration": {"policy": DEFAULT_POLICY},
                "tags": ["maintenance", "decision"],
            },
            {
                "name": EVIDENCE_MODEL,
                "provider": "asset-condition-evidence",
                "description": "同一份分析的逐條依據，供「Why?」面板與 LLM 推理使用。",
                "configuration": {"policy": DEFAULT_POLICY, "required_only": False},
                "tags": ["maintenance", "decision"],
            },
            {
                "name": REASONING_MODEL,
                "provider": "llm-reasoning",
                "description": (
                    "把結構化證據寫成一段可讀的判斷說明，每句話都必須指回一條證據。"
                    "未設定語言模型端點時，改由證據直接組成，並標明來源。"
                ),
                "configuration": {
                    "question": "這台設備是否需要處置？依據是什麼？",
                    "subject_column": "asset_id",
                    "statement_column": "statement",
                    "severity_column": "contribution",
                    "category_column": "analyzer",
                    "conclusion_column": "recommended_action",
                    "language": "zh-TW",
                    "max_evidence": 25,
                    "system": (
                        "You are a reliability engineer writing for a maintenance "
                        "planner. Findings come from threshold, baseline, trend, "
                        "statistical, failure-signature, policy, history and data-"
                        "quality analyzers. A negative contribution argues against "
                        "acting."
                    ),
                },
                "tags": ["maintenance", "llm"],
            },
            {
                "name": PROJECTION_MODEL,
                "provider": "threshold-projection",
                "description": (
                    "把每個量測的門檻進度外推到警戒與緊急界線，回報的是"
                    "區間與依據等級（已越線／可估計／僅能推斷／無法判斷），"
                    "不是一個假裝精準的日期。"
                ),
                "configuration": {
                    "value": F.C_LIMIT_PROGRESS,
                    "time": F.C_DAY,
                    "group_by": [F.C_ASSET, F.C_PARAMETER],
                    "limits": [
                        {"name": "警戒", "value": 33.4},
                        {"name": "嚴重", "value": 63.6},
                        {"name": "緊急", "value": 100.0},
                    ],
                    "direction": "rising",
                    "window": F.LONG_WINDOW,
                    "min_points": 8,
                    "min_r_squared": 0.3,
                    "horizon": 180,
                },
                "tags": ["maintenance", "projection"],
            },
            {
                "name": HEALTH_SCORECARD_MODEL,
                "provider": "scorecard",
                "description": (
                    "以量測層級示範健康評分卡：門檻進度、偏離幅度、趨勢與"
                    "資料品質加權，並回報實際能算到多少證據。"
                ),
                "configuration": {
                    "components": [
                        {"name": "門檻進度", "column": F.C_LIMIT_PROGRESS,
                         "kind": "linear", "good": 0, "bad": 100, "weight": 3,
                         "description": "距離緊急界線還有多遠"},
                        {"name": "偏離幅度", "column": F.C_DEVIATION,
                         "kind": "linear", "good": 0, "bad": 40, "weight": 2,
                         "description": "扣掉負載與環境後仍偏離多少"},
                        {"name": "劣化趨勢", "column": F.C_PROGRESS_SLOPE,
                         "kind": "linear", "good": 0, "bad": 1.5, "weight": 2,
                         "missing": "neutral", "neutral_score": 80,
                         "description": "每天往界線推進幾個百分點"},
                        {"name": "取樣充足度", "column": F.C_SAMPLES,
                         "kind": "linear", "good": 24, "bad": 2, "weight": 1,
                         "description": "當天有幾個運轉小時的讀值"},
                    ],
                    "bands": [
                        {"upto": 40, "label": "CRITICAL"},
                        {"upto": 60, "label": "POOR"},
                        {"upto": 75, "label": "DEGRADED"},
                        {"upto": 88, "label": "WATCH"},
                        {"upto": None, "label": "HEALTHY"},
                    ],
                    "output": "measurement_health",
                    "min_coverage": 0.4,
                },
                "tags": ["maintenance", "health"],
            },
            {
                "name": RISK_MATRIX_MODEL,
                "provider": "risk-matrix",
                "description": (
                    "可能性 × 後果的 3×4 風險矩陣。格子是簽核過的資料，"
                    "所以「為什麼是 HIGH」永遠指得出是哪一格。"
                ),
                "configuration": {
                    "likelihood": {
                        "column": F.C_LIMIT_PROGRESS,
                        "levels": ["low", "medium", "high"],
                        "bands": [33.4, 66.7],
                        "default": "low",
                    },
                    "consequence": {
                        "column": F.C_CRITICALITY,
                        "levels": ["low", "medium", "high", "critical"],
                        "default": "medium",
                    },
                    "grid": [
                        ["LOW", "LOW", "MEDIUM", "HIGH"],
                        ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                        ["MEDIUM", "HIGH", "CRITICAL", "CRITICAL"],
                    ],
                    "severity_order": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    "output": "measurement_risk",
                },
                "tags": ["maintenance", "risk"],
            },
        ],
        applications=[
            {
                "name": APPLICATION,
                "description": (
                    "四十台設備、八種類型、三個廠區的預防性維護分析：健康評估、"
                    "風險分級、維修窗口與逐條判斷依據。不使用機器學習——"
                    "每一項結論都指得出是哪一條證據支持的。"
                ),
                "kind": "builtin",
                "models": [
                    DECISION_MODEL,
                    EVIDENCE_MODEL,
                    REASONING_MODEL,
                    PROJECTION_MODEL,
                    HEALTH_SCORECARD_MODEL,
                    RISK_MATRIX_MODEL,
                ],
                "datasets": [
                    F.ASSETS_DATASET,
                    F.DAILY_FEATURES,
                    F.MAINTENANCE_DATASET,
                    F.FAILURE_DATASET,
                    F.THRESHOLD_DATASET,
                    F.POLICY_DATASET,
                    F.RULES_DATASET,
                ],
                #  Taken from the code seeder rather than written out, because
                #  the dashboards exist only once the charts have been computed.
                "dashboards": [board["name"] for board in DASHBOARDS],
                "entrypoint": "/applications/asset-maintenance",
                "configuration": {
                    "default_policy": DEFAULT_POLICY,
                    "horizon_days": 14,
                },
                "publish": True,
            }
        ],
    )
