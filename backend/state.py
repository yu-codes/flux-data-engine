"""全域應用程式狀態"""

from pathlib import Path

from data_pipiline.stage09_inference_pipeline.typhoon.predict import (
    DisasterImpactPipeline,
)
from data_pipiline.stage08_downstream_analysis.typhoon.rainfall import RainfallAnalyzer

# Combined RRF 僅保留可選降水訊號的版本（combined_rainfall）
SUPPORTED_METHODS = [
    "coastline_rrf",
    "coastline",
    "combined_rainfall",
    "knn_optimized",
    "rule_based",
]
# 海岸線外擴緩衝預設半徑 (km) — 所有方法共用的計算範圍
DEFAULT_BUFFER_KM = 500.0
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
            # 所有方法共用「海岸線外擴 buffer_km」作為計算範圍
            params = {"k": 5, "buffer_km": DEFAULT_BUFFER_KM}
            # coastline_rrf：絕對位置(0.8)+KNN(0.2) RRF 融合，最佳化 rrf_k=60
            if method == "coastline_rrf":
                params.update(
                    {
                        "weight_coastline": 0.80,
                        "weight_knn": 0.20,
                        "rrf_k": 60,
                        "pool_size_factor": 12,
                    }
                )
            # combined_rainfall：預設啟用降水訊號（臺南），前端可切換
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
