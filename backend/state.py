"""全域應用程式狀態"""

from pathlib import Path

from data_pipiline.stage09_inference_pipeline.typhoon.predict import (
    DisasterImpactPipeline,
)
from data_pipiline.stage08_downstream_analysis.typhoon.rainfall import RainfallAnalyzer

SUPPORTED_METHODS = [
    "combined",
    "combined_optimized",
    "combined_rainfall",
    "knn_optimized",
    "rule_based",
]
DATA_DIR = "data/typhoon/preprocessed"
# 即時預測使用完整 207 筆颱風資料
REALTIME_DATASET = "typhoons_overview.json"


class AppState:
    def __init__(self):
        self.pipelines: dict[str, DisasterImpactPipeline] = {}
        self.rainfall_analyzer: RainfallAnalyzer | None = None

    def initialize(self):
        """預載所有模型（即時預測：完整 207 筆）"""
        for method in SUPPORTED_METHODS:
            params = {"k": 5}
            # combined_rainfall：預設啟用降水訊號（臺南）
            if method == "combined_rainfall":
                params.update(
                    {
                        "use_rainfall": True,
                        "rainfall_region": "tn",
                        "rainfall_weight": 0.15,
                    }
                )
            config = {
                "method": method,
                "parameters": params,
                "evaluation": {
                    "categories": ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
                },
            }
            p = DisasterImpactPipeline(config=config)
            p.initialize(DATA_DIR, dataset_filename=REALTIME_DATASET)
            self.pipelines[method] = p

        try:
            # 降水分析器共用任一 pipeline 已載入的 loader（資料源統一為 overview）
            any_pipeline = next(iter(self.pipelines.values()), None)
            loader = any_pipeline.loader if any_pipeline else None
            self.rainfall_analyzer = RainfallAnalyzer(loader=loader, processed_dir=DATA_DIR)
            self.rainfall_analyzer.load()
        except Exception:
            self.rainfall_analyzer = None

    def cleanup(self):
        self.pipelines.clear()


app_state = AppState()
