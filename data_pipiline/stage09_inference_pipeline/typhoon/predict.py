"""
預測管道 — 組裝模組化元件，支援 config 驅動

00_data_ingestion → 04_feature_engineering → 05_modeling → 06_evaluation

每次預測由外部 config 決定方法與參數組合，
pipeline 僅負責組裝與執行。
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from data_pipiline.stage00_data_ingestion.typhoon.loader import DataLoader
from data_pipiline.stage04_feature_engineering.typhoon.extractor import (
    TyphoonFeatureExtractor,
    TyphoonFeatures,
)
from data_pipiline.stage05_model_training.typhoon.similarity.base import (
    SimilarityBase,
    SimilarityResult,
)
from data_pipiline.stage05_model_training.typhoon.similarity.knn import KNNSimilarity
from data_pipiline.stage05_model_training.typhoon.similarity.dtw import DTWSimilarity
from data_pipiline.stage05_model_training.typhoon.similarity.combined import (
    CombinedSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.similarity.baseline import (
    BaselineSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.similarity.rule_based import (
    RuleBasedSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.similarity.coastline import (
    CoastlineSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.similarity.coastline_rrf import (
    CoastlineRRFSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.analog import AnalogModel
from data_pipiline.stage05_model_training.typhoon.mapping import ImpactMapper
from data_pipiline.stage07_model_evaluation.typhoon.metrics import (
    compute_category_accuracy,
)
from data_pipiline.stage00_data_ingestion.typhoon.regions import DEFAULT_REGION

# 只評估有明確路徑定義的類別
VALID_CATEGORIES = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]


@dataclass
class PredictionResult:
    """單一颱風的預測結果"""

    typhoon_id: str
    name_zh: str
    name_en: str
    true_category: str | None
    predicted_category: str | None
    confidence: float
    is_correct: bool | None
    similar_typhoons: list[dict]
    category_votes: dict[str, float]


class DisasterImpactPipeline:
    """
    颱風類比預測管道

    可由外部 config dict 初始化，支援：
    - method: knn / dtw / combined / rule_based / baseline
    - 各方法的專屬參數
    """

    def __init__(self, config: dict | None = None, **kwargs):
        """
        Args:
            config: 完整配置 dict（通常從 YAML 載入）
            **kwargs: 簡易模式，相容舊 API
        """
        if config:
            self._config = config
            params = config.get("parameters", {})
            self.similarity_method = config["method"]
            self.alpha = params.get("alpha", 0.2)
            self.rule_weight = params.get("rule_weight", 0.5)
            self.impact_radius_km = params.get("impact_radius_km", 500.0)
            self.k = params.get("k", 5)
            self.pool_size_factor = params.get("pool_size_factor", 10)
            self.rrf_k = params.get("rrf_k", 60)
            self.dtw_weights = params.get("dtw_weights")
            self.feature_weights = params.get("feature_weights")
            self.use_rainfall = params.get("use_rainfall", False)
            self.rainfall_region = params.get("rainfall_region", DEFAULT_REGION)
            self.rainfall_weight = params.get("rainfall_weight", 0.15)
            self.weight_path = params.get("weight_path", 0.4)
            self.weight_category = params.get("weight_category", 0.5)
            self.weight_intensity = params.get("weight_intensity", 0.1)
            self.buffer_km = params.get("buffer_km", 500.0)
            # coastline_rrf 專屬權重（絕對位置為主）
            self.weight_coastline = params.get("weight_coastline", 0.80)
            self.weight_knn = params.get("weight_knn", 0.20)
            self.weight_rainfall_rrf = params.get("weight_rainfall_rrf", 0.08)
            eval_cfg = config.get("evaluation", {})
            self.valid_categories = eval_cfg.get("categories", VALID_CATEGORIES)
            self.metrics = eval_cfg.get("metrics", ["category_accuracy"])
        else:
            # Legacy kwargs mode
            self.similarity_method = kwargs.get("similarity_method", "combined")
            self.alpha = kwargs.get("alpha", 0.2)
            self.rule_weight = kwargs.get("rule_weight", 0.5)
            self.impact_radius_km = kwargs.get("impact_radius_km", 500.0)
            self.k = kwargs.get("k", 5)
            self.pool_size_factor = kwargs.get("pool_size_factor", 10)
            self.rrf_k = kwargs.get("rrf_k", 60)
            self.dtw_weights = kwargs.get("dtw_weights")
            self.feature_weights = kwargs.get("feature_weights")
            self.use_rainfall = kwargs.get("use_rainfall", False)
            self.rainfall_region = kwargs.get("rainfall_region", DEFAULT_REGION)
            self.rainfall_weight = kwargs.get("rainfall_weight", 0.15)
            self.weight_path = kwargs.get("weight_path", 0.4)
            self.weight_category = kwargs.get("weight_category", 0.5)
            self.weight_intensity = kwargs.get("weight_intensity", 0.1)
            self.buffer_km = kwargs.get("buffer_km", 500.0)
            self.weight_coastline = kwargs.get("weight_coastline", 0.80)
            self.weight_knn = kwargs.get("weight_knn", 0.20)
            self.weight_rainfall_rrf = kwargs.get("weight_rainfall_rrf", 0.08)
            self.valid_categories = VALID_CATEGORIES
            self.metrics = ["category_accuracy"]
            self._config = self._build_config()

        self.loader: DataLoader | None = None
        # 計算範圍統一以「海岸線外擴 buffer_km」框 impact window（所有方法共用）
        self.extractor = TyphoonFeatureExtractor(
            impact_radius_km=self.impact_radius_km, buffer_km=self.buffer_km
        )
        self.similarity: SimilarityBase | None = None
        self.model: AnalogModel | None = None
        self.features: dict[str, TyphoonFeatures] = {}
        self.label_dict: dict[str, str] = {}
        self._fitted_buffer_km: float | None = None  # 目前已擬合的 buffer（快取用）

    def _build_config(self) -> dict:
        """從屬性建構 config dict"""
        return {
            "method": self.similarity_method,
            "parameters": {
                "alpha": self.alpha,
                "k": self.k,
                "impact_radius_km": self.impact_radius_km,
                "pool_size_factor": self.pool_size_factor,
                "rrf_k": self.rrf_k,
                "use_rainfall": self.use_rainfall,
                "rainfall_region": self.rainfall_region,
                "rainfall_weight": self.rainfall_weight,
            },
            "evaluation": {
                "metrics": self.metrics,
                "categories": self.valid_categories,
            },
        }

    def get_config(self) -> dict:
        """取得完整配置（用於記錄）"""
        return self._config

    def initialize(
        self,
        processed_dir: str = "data/typhoon/preprocessed",
        dataset_filename: str = "typhoons_overview.json",
    ):
        """載入資料並建立模型

        Args:
            processed_dir: preprocessed 目錄
            dataset_filename: 資料檔名（即時預測用完整 207 筆；批次評估可指定 198 筆子集）
        """
        print("=" * 60)
        print("🌀 初始化颱風類比預測系統")
        print("=" * 60)

        # 1. 載入資料
        print("\n📂 載入資料...")
        self.loader = DataLoader(processed_dir, filename=dataset_filename)
        self.loader.load()

        # 2. 提取特徵
        print("\n🔧 提取特徵...")
        self.features = self.extractor.extract_all(self.loader)

        # 3. 建立標籤
        self.label_dict = ImpactMapper.build_label_dict(self.loader)

        # 4. 建立相似度模型
        print("\n📐 建立相似度模型...")
        self.similarity = self._create_similarity()
        self._fit_similarity()

        # 5. 建立預測模型
        self.model = AnalogModel(label_dict=self.label_dict)

        self._fitted_buffer_km = self.buffer_km
        print(f"\n✅ 系統初始化完成（方法={self.similarity_method}）")

    def set_buffer_km(self, buffer_km: float):
        """
        調整計算範圍（海岸線外擴 buffer_km）。

        - 與目前已擬合的 buffer 相同 → 直接沿用快取（即時）
        - 不同 → 重新擷取參考特徵 + 重新擬合相似度（約數秒）
        - coastline 方法：僅需更新 similarity 的 buffer（重新裁切，便宜）
        """
        if buffer_km == self._fitted_buffer_km:
            return
        self.buffer_km = buffer_km

        if self.similarity_method == "coastline":
            self.similarity.buffer_km = buffer_km
            self._fitted_buffer_km = buffer_km
            return

        # 其他方法：impact window 由海岸線 buffer 決定 → 重新擷取 + 擬合
        self.extractor = TyphoonFeatureExtractor(
            impact_radius_km=self.impact_radius_km, buffer_km=buffer_km
        )
        self.features = self.extractor.extract_all(self.loader)
        self.similarity = self._create_similarity()
        self._fit_similarity()
        self.model = AnalogModel(label_dict=self.label_dict)
        self._fitted_buffer_km = buffer_km

    def _create_similarity(self) -> SimilarityBase:
        """根據配置建立相似度計算器"""
        method = self.similarity_method
        if method == "knn":
            return KNNSimilarity(feature_weights=self.feature_weights)
        elif method == "dtw":
            return DTWSimilarity(
                dtw_weights=(np.array(self.dtw_weights) if self.dtw_weights else None)
            )
        elif method == "combined":
            return CombinedSimilarity(
                alpha=self.alpha,
                rule_weight=self.rule_weight,
                feature_weights=self.feature_weights,
                dtw_weights=(np.array(self.dtw_weights) if self.dtw_weights else None),
                pool_size_factor=self.pool_size_factor,
                rrf_k=self.rrf_k,
                use_rainfall=self.use_rainfall,
                rainfall_region=self.rainfall_region,
                rainfall_weight=self.rainfall_weight,
                buffer_km=self.buffer_km,
            )
        elif method == "rule_based":
            return RuleBasedSimilarity(
                weight_path=self.weight_path,
                weight_category=self.weight_category,
                weight_intensity=self.weight_intensity,
                buffer_km=self.buffer_km,
            )
        elif method == "knn_optimized":
            # 只對3個顯著特徵加大權重 (indices 0,1,8)
            optimized_weights = np.array(
                [3.0, 2.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.5, 0.5, 0.5]
            )
            return KNNSimilarity(
                feature_weights=self.feature_weights or optimized_weights
            )
        elif method in ("combined_optimized", "combined_rainfall"):
            # 最佳 RRF 參數 + 最佳 DTW 權重 + 特徵加權 KNN
            # combined_rainfall：在優化版基礎上強制啟用降水訊號
            optimized_fw = np.array(
                [3.0, 2.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.5, 0.5, 0.5]
            )
            use_rain = self.use_rainfall or method == "combined_rainfall"
            return CombinedSimilarity(
                alpha=self.alpha if self.alpha != 0.2 else 0.10,
                rule_weight=self.rule_weight if self.rule_weight != 0.5 else 0.40,
                feature_weights=self.feature_weights or optimized_fw,
                dtw_weights=(
                    np.array(self.dtw_weights)
                    if self.dtw_weights
                    else np.array([1.0, 0.5, 1.0, 0.5])
                ),
                pool_size_factor=self.pool_size_factor,
                rrf_k=self.rrf_k if self.rrf_k != 60 else 30,
                use_rainfall=use_rain,
                rainfall_region=self.rainfall_region,
                rainfall_weight=self.rainfall_weight,
                buffer_km=self.buffer_km,
            )
        elif method == "coastline":
            return CoastlineSimilarity(buffer_km=self.buffer_km)
        elif method == "coastline_rrf":
            optimized_fw = np.array(
                [3.0, 2.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.5, 0.5, 0.5]
            )
            return CoastlineRRFSimilarity(
                buffer_km=self.buffer_km,
                w_coastline=self.weight_coastline,
                w_knn=self.weight_knn,
                w_rainfall=self.weight_rainfall_rrf,
                feature_weights=self.feature_weights or optimized_fw,
                pool_size_factor=self.pool_size_factor if self.pool_size_factor != 10 else 12,
                rrf_k=self.rrf_k,  # 最佳化：rrf_k=60
                use_rainfall=self.use_rainfall,
                rainfall_region=self.rainfall_region,
            )
        elif method == "baseline":
            return BaselineSimilarity(seed=42)
        else:
            raise ValueError(f"不支援的方法：{method}")

    def _fit_similarity(self):
        """擬合相似度模型"""
        if self.similarity_method in (
            "rule_based",
            "combined",
            "combined_optimized",
            "combined_rainfall",
            "coastline",
            "coastline_rrf",
        ):
            self.similarity.fit(self.features, loader=self.loader)
        else:
            self.similarity.fit(self.features)

    def predict(self, query_id: str, k: int | None = None) -> PredictionResult:
        """對單一颱風做預測"""
        if k is None:
            k = self.k
        rec = self.loader.get(query_id)

        # Rule-based: 直接使用規則分類結果（不需要投票）
        if self.similarity_method == "rule_based":
            from data_pipiline.stage05_model_training.typhoon.similarity.rule_based import (
                classify_typhoon_by_rules,
            )

            rule_result = classify_typhoon_by_rules(
                rec.track, rec.landfall_location, buffer_km=self.buffer_km
            )
            predicted_cat = rule_result["predicted_category"]
            conf = rule_result["confidence"]
            # Still get similar typhoons for reference
            sim_result = self.similarity.find_similar(query_id, k=k)
            similar_info = []
            for tid, dist in zip(sim_result.similar_ids, sim_result.distances):
                analog_rec = self.loader.get(tid)
                similar_info.append(
                    {
                        "typhoon_id": tid,
                        "name_zh": analog_rec.name_zh,
                        "name_en": analog_rec.name_en,
                        "year": analog_rec.year,
                        "category": analog_rec.taiwan_track_category,
                        "distance": round(dist, 4),
                    }
                )
            return PredictionResult(
                typhoon_id=query_id,
                name_zh=rec.name_zh,
                name_en=rec.name_en,
                true_category=rec.taiwan_track_category,
                predicted_category=predicted_cat,
                confidence=conf,
                is_correct=(predicted_cat == rec.taiwan_track_category),
                similar_typhoons=similar_info,
                category_votes={predicted_cat: 1.0},
            )

        sim_result = self.similarity.find_similar(query_id, k=k)

        # coastline 的 distances 為 km，量級過大會使 exp(-d) 下溢；
        # 改用正規化距離（1 - score）做類比投票。
        if self.similarity_method == "coastline":
            vote_distances = [1.0 - s for s in sim_result.scores]
        else:
            vote_distances = sim_result.distances

        pred = self.model.predict(
            query_id=query_id,
            similar_ids=sim_result.similar_ids,
            distances=vote_distances,
        )

        similar_info = []
        for analog in pred.get("analogs", []):
            tid = analog["typhoon_id"]
            analog_rec = self.loader.get(tid)
            similar_info.append(
                {
                    "typhoon_id": tid,
                    "name_zh": analog_rec.name_zh,
                    "name_en": analog_rec.name_en,
                    "year": analog_rec.year,
                    "category": analog.get("category"),
                    "distance": analog.get("distance"),
                }
            )

        return PredictionResult(
            typhoon_id=query_id,
            name_zh=rec.name_zh,
            name_en=rec.name_en,
            true_category=rec.taiwan_track_category,
            predicted_category=pred.get("predicted_category"),
            confidence=pred.get("confidence", 0.0),
            is_correct=pred.get("is_correct"),
            similar_typhoons=similar_info,
            category_votes=pred.get("category_votes", {}),
        )

    def evaluate(self, k: int | None = None, verbose: bool = True) -> dict[str, Any]:
        """
        Leave-one-out 評估（只評估 valid_categories 內的類別）
        """
        if k is None:
            k = self.k

        all_ids = self.loader.get_all_ids()
        results: list[PredictionResult] = []
        predictions_for_metrics: list[dict] = []

        for tid in all_ids:
            rec = self.loader.get(tid)
            # 只評估 valid categories
            if rec.taiwan_track_category not in self.valid_categories:
                continue

            result = self.predict(tid, k=k)
            results.append(result)
            predictions_for_metrics.append(
                {
                    "typhoon_id": result.typhoon_id,
                    "true_category": result.true_category,
                    "predicted_category": result.predicted_category,
                }
            )

        # 計算指標
        eval_result = compute_category_accuracy(
            predictions_for_metrics, self.valid_categories
        )

        if verbose:
            print(f"\n{'='*60}")
            print(f"📊 評估結果（k={k}, method={self.similarity_method}）")
            print(f"{'='*60}")
            print(
                f"  總準確率：{eval_result.overall_score:.1%}"
                f" ({eval_result.correct}/{eval_result.total})"
            )
            print(f"\n  各類準確率：")
            for cat in sorted(eval_result.per_category.keys()):
                info = eval_result.per_category[cat]
                print(
                    f"    類型 {cat}: {info['accuracy']:.1%}"
                    f" ({info['correct']}/{info['total']})"
                )

        return {
            "accuracy": eval_result.overall_score,
            "total": eval_result.total,
            "correct": eval_result.correct,
            "per_category": eval_result.per_category,
            "predictions": results,
            "confusion_data": eval_result.confusion_data,
        }

    def save_results(self, eval_result: dict, output_dir: str):
        """儲存評估結果"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # 預測明細
        details = []
        for r in eval_result["predictions"]:
            details.append(
                {
                    "typhoon_id": r.typhoon_id,
                    "name_zh": r.name_zh,
                    "name_en": r.name_en,
                    "true_category": r.true_category,
                    "predicted_category": r.predicted_category,
                    "confidence": round(r.confidence, 4),
                    "is_correct": r.is_correct,
                    "similar_typhoons": r.similar_typhoons,
                    "category_votes": {
                        k: round(v, 4) for k, v in r.category_votes.items()
                    },
                }
            )

        with open(out / "prediction_details.json", "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False, indent=2)

        # 摘要
        summary = {
            "method": self.similarity_method,
            "accuracy": round(eval_result["accuracy"], 4),
            "total": eval_result["total"],
            "correct": eval_result["correct"],
            "per_category": eval_result["per_category"],
        }
        with open(out / "evaluation_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        # 配置檔（關鍵：每次結果可追溯到配置）
        with open(out / "config.json", "w", encoding="utf-8") as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

        print(f"✓ 結果已儲存至 {out}/")
