"""全域應用程式狀態"""

from pathlib import Path

from src.stage09_inference_pipeline.typhoon.predict import DisasterImpactPipeline
from src.stage08_downstream_analysis.typhoon.rainfall import RainfallAnalyzer

SUPPORTED_METHODS = ["combined", "combined_optimized", "knn_optimized", "rule_based"]
DATA_DIR = "data/typhoon/preprocessed"


class AppState:
    def __init__(self):
        self.pipelines: dict[str, DisasterImpactPipeline] = {}
        self.rainfall_analyzer: RainfallAnalyzer | None = None

    def initialize(self):
        """預載所有模型"""
        for method in SUPPORTED_METHODS:
            config = {
                "method": method,
                "parameters": {"k": 5},
                "evaluation": {
                    "categories": ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
                },
            }
            p = DisasterImpactPipeline(config=config)
            p.initialize(DATA_DIR)
            self.pipelines[method] = p

        try:
            self.rainfall_analyzer = RainfallAnalyzer()
            self.rainfall_analyzer.load()
        except Exception:
            self.rainfall_analyzer = None

    def cleanup(self):
        self.pipelines.clear()


app_state = AppState()
