"""
Combined Similarity — KNN + DTW + Rule-Based (+ Rainfall) with Reciprocal Rank Fusion

使用 RRF 將多組排名融合：
1. KNN: 11 維摘要特徵向量 → 歐式距離排名
2. DTW: 極座標時序路徑 → 物理標準化 DTW 距離排名
3. Rule-Based: 規則分類 → 同類颱風優先排名
4. Rainfall（可選）: 指定地區事件降水 → 降水量相近優先排名

Pipeline:
  各組排名各自獨立計算，再以 RRF 融合 → 最終 Top-k

降水訊號（use_rainfall=True）將指定地區的事件降水納入相似度，
作為第 4 個排名來源；關閉時行為與原三訊號版本完全一致。
"""

import numpy as np
from .base import SimilarityBase, SimilarityResult
from .knn import KNNSimilarity
from .dtw import DTWSimilarity
from .rule_based import classify_typhoon_by_rules
from .regions import DEFAULT_REGION


class CombinedSimilarity(SimilarityBase):
    """
    KNN + DTW + Rule-Based (+ Rainfall) Reciprocal Rank Fusion

    score(tid) = w_knn/(rrf_k+rank_knn) + w_dtw/(rrf_k+rank_dtw)
               + w_rule/(rrf_k+rank_rule) + w_rain/(rrf_k+rank_rain)

    其中 w_dtw = 1 - alpha - rule_weight - (rainfall_weight if use_rainfall else 0)
    """

    def __init__(
        self,
        alpha: float = 0.2,
        rule_weight: float = 0.5,
        feature_weights: np.ndarray | None = None,
        dtw_weights: np.ndarray | None = None,
        pool_size_factor: int = 10,
        rrf_k: int = 60,
        use_rainfall: bool = False,
        rainfall_region: str = DEFAULT_REGION,
        rainfall_weight: float = 0.15,
        buffer_km: float | None = None,
    ):
        self.alpha = alpha
        self.rule_weight = rule_weight
        self.pool_size_factor = pool_size_factor
        self.rrf_k = rrf_k
        self.buffer_km = buffer_km
        self.use_rainfall = use_rainfall
        self.rainfall_region = rainfall_region
        self.rainfall_weight = rainfall_weight if use_rainfall else 0.0
        self.knn = KNNSimilarity(feature_weights=feature_weights)
        self.dtw = DTWSimilarity(dtw_weights=dtw_weights)
        self._ids: list[str] = []
        self._features_dict = {}
        self._rule_categories: dict[str, str] = {}  # tid -> rule-predicted category
        self._rainfall: dict[str, float | None] = {}  # tid -> 指定地區降水量
        self._loader = None

    def fit(self, feature_dict: dict, **kwargs):
        self._features_dict = feature_dict
        self._ids = list(feature_dict.keys())
        self._loader = kwargs.get("loader")
        self.knn.fit(feature_dict)
        self.dtw.fit(feature_dict)

        # Pre-compute rule-based classification + rainfall for all typhoons
        if self._loader:
            for tid in self._ids:
                rec = self._loader.get(tid)
                result = classify_typhoon_by_rules(
                    rec.track, rec.landfall_location, buffer_km=self.buffer_km
                )
                self._rule_categories[tid] = result["predicted_category"]
                self._rainfall[tid] = rec.get_rainfall(self.rainfall_region)

        w_dtw = max(0.0, 1.0 - self.alpha - self.rule_weight - self.rainfall_weight)
        rain_msg = (
            f", Rain[{self.rainfall_region}]={self.rainfall_weight:.2f}"
            if self.use_rainfall
            else ""
        )
        print(
            f"✓ Combined RRF 已擬合 {len(self._ids)} 筆"
            f"（KNN={self.alpha:.2f}, DTW={w_dtw:.2f}, Rule={self.rule_weight:.2f}{rain_msg}）"
        )

    def configure_rainfall(
        self,
        use_rainfall: bool,
        region: str | None = None,
        weight: float | None = None,
    ):
        """執行期切換降水訊號設定（重新取用對應地區降水，供 backend 逐請求調整）"""
        self.use_rainfall = use_rainfall
        if region is not None:
            self.rainfall_region = region
        if weight is not None:
            self.rainfall_weight = weight if use_rainfall else 0.0
        else:
            self.rainfall_weight = self.rainfall_weight if use_rainfall else 0.0
        if use_rainfall and self._loader:
            self._rainfall = {
                tid: self._loader.get(tid).get_rainfall(self.rainfall_region)
                for tid in self._ids
            }

    def compute_distance(self, id_a: str, id_b: str) -> float:
        knn_d = self.knn.compute_distance(id_a, id_b)
        dtw_d = self.dtw.compute_distance(id_a, id_b)
        return self.alpha * knn_d + (1 - self.alpha) * dtw_d

    def _rainfall_ranks(self, query_rain: float | None) -> dict[str, int]:
        """依與 query 降水量的差距排名（差距小→排前），無降水資料者排最後"""
        if query_rain is None or not self._rainfall:
            return {}
        scored = []
        missing = []
        for tid in self._ids:
            r = self._rainfall.get(tid)
            if r is None:
                missing.append(tid)
            else:
                scored.append((tid, abs(query_rain - r)))
        scored.sort(key=lambda x: x[1])
        ranks = {tid: rank for rank, (tid, _) in enumerate(scored)}
        for rank, tid in enumerate(missing, len(scored)):
            ranks[tid] = rank
        return ranks

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        if query_id not in self._features_dict:
            raise KeyError(f"找不到颱風：{query_id}")

        pool_size = min(len(self._ids) - 1, k * self.pool_size_factor)

        knn_result = self.knn.find_similar(query_id, pool_size, exclude_self)
        dtw_result = self.dtw.find_similar(query_id, pool_size, exclude_self)

        # Build rank maps
        knn_ranks = {tid: rank for rank, tid in enumerate(knn_result.similar_ids)}
        dtw_ranks = {tid: rank for rank, tid in enumerate(dtw_result.similar_ids)}

        # Build rule-based rank: same category typhoons rank first
        query_rule_cat = self._rule_categories.get(query_id, "")
        rule_ranks = {}
        same_cat_ids: list[str] = []
        if self._rule_categories:
            same_cat_ids = [
                t
                for t in self._ids
                if t != query_id and self._rule_categories.get(t) == query_rule_cat
            ]
            diff_cat_ids = [
                t
                for t in self._ids
                if t != query_id and self._rule_categories.get(t) != query_rule_cat
            ]
            for rank, tid in enumerate(same_cat_ids):
                rule_ranks[tid] = rank
            for rank, tid in enumerate(diff_cat_ids, len(same_cat_ids)):
                rule_ranks[tid] = rank

        # Build rainfall rank（可選）
        rain_ranks = {}
        rain_active = False
        if self.use_rainfall:
            query_rain = self._rainfall.get(query_id)
            rain_ranks = self._rainfall_ranks(query_rain)
            rain_active = bool(rain_ranks)

        # Merge candidates
        candidates = set(knn_result.similar_ids + dtw_result.similar_ids)
        if self._rule_categories:
            candidates |= set(same_cat_ids[:pool_size])
        if rain_active:
            # 加入降水最相近的前 pool_size 個颱風
            top_rain = sorted(rain_ranks, key=rain_ranks.get)[:pool_size]
            candidates |= set(top_rain)
        if exclude_self:
            candidates.discard(query_id)

        # RRF scoring
        w_knn = self.alpha
        w_rule = self.rule_weight
        w_rain = self.rainfall_weight if rain_active else 0.0
        w_dtw = max(0.0, 1.0 - self.alpha - self.rule_weight - w_rain)

        combined = {}
        for tid in candidates:
            knn_rank = knn_ranks.get(tid, pool_size)
            dtw_rank = dtw_ranks.get(tid, pool_size)
            rule_rank = rule_ranks.get(tid, len(self._ids))
            rrf_score = (
                w_knn / (self.rrf_k + knn_rank)
                + w_dtw / (self.rrf_k + dtw_rank)
                + w_rule / (self.rrf_k + rule_rank)
            )
            if w_rain > 0:
                rain_rank = rain_ranks.get(tid, len(self._ids))
                rrf_score += w_rain / (self.rrf_k + rain_rank)
            combined[tid] = rrf_score

        sorted_items = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:k]

        result_ids = [item[0] for item in sorted_items]
        result_scores = [item[1] for item in sorted_items]
        max_s = max(result_scores) if result_scores else 1.0
        scores = [s / (max_s + 1e-8) for s in result_scores]
        distances = [1.0 - s for s in scores]

        return SimilarityResult(
            query_id=query_id,
            similar_ids=result_ids,
            distances=distances,
            scores=scores,
        )

    def get_config(self) -> dict:
        """返回當前配置"""
        return {
            "method": "combined_rrf",
            "alpha": self.alpha,
            "rule_weight": self.rule_weight,
            "pool_size_factor": self.pool_size_factor,
            "rrf_k": self.rrf_k,
            "use_rainfall": self.use_rainfall,
            "rainfall_region": self.rainfall_region,
            "rainfall_weight": self.rainfall_weight,
        }

    def find_similar_by_vector(
        self,
        query_vec: np.ndarray,
        k: int = 5,
        query_features=None,
        query_rainfall: float | None = None,
    ) -> SimilarityResult:
        """
        用特徵向量查詢（支援即時預測的新颱風）

        Args:
            query_vec: 11 維特徵向量（用於 KNN）
            k: 返回前 k 個
            query_features: TyphoonFeatures 物件（用於 DTW，可選）
            query_rainfall: 查詢颱風在指定地區的預期降水量（用於降水訊號，可選）
        """
        pool_size = min(len(self._ids), k * self.pool_size_factor)

        # 1. KNN ranking
        knn_result = self.knn.find_similar_by_vector(query_vec, k=pool_size)
        knn_ranks = {tid: rank for rank, tid in enumerate(knn_result.similar_ids)}

        # 2. DTW ranking（若有 features）
        dtw_ranks = {}
        if query_features is not None:
            try:
                mat = query_features.get_impact_window_matrix()
                dtw_result = self.dtw.find_similar_by_matrix(mat, k=pool_size)
                dtw_ranks = {
                    tid: rank for rank, tid in enumerate(dtw_result.similar_ids)
                }
            except Exception:
                pass

        # 3. Rule-Based ranking（用 KNN top-1 的分類做 proxy）
        rule_ranks = {}
        if self._rule_categories and knn_result.similar_ids:
            proxy_cat = self._rule_categories.get(knn_result.similar_ids[0], "")
            same_cat_ids = [
                t for t in self._ids if self._rule_categories.get(t) == proxy_cat
            ]
            diff_cat_ids = [
                t for t in self._ids if self._rule_categories.get(t) != proxy_cat
            ]
            for rank, tid in enumerate(same_cat_ids):
                rule_ranks[tid] = rank
            for rank, tid in enumerate(diff_cat_ids, len(same_cat_ids)):
                rule_ranks[tid] = rank

        # 4. Rainfall ranking（需提供 query_rainfall）
        rain_ranks = {}
        rain_active = False
        if self.use_rainfall and query_rainfall is not None:
            rain_ranks = self._rainfall_ranks(query_rainfall)
            rain_active = bool(rain_ranks)

        # Merge candidates
        candidates = set(knn_result.similar_ids)
        if dtw_ranks:
            candidates |= set(list(dtw_ranks.keys())[:pool_size])
        if rule_ranks:
            candidates |= set(
                t for t in self._ids if rule_ranks.get(t, len(self._ids)) < pool_size
            )
        if rain_active:
            top_rain = sorted(rain_ranks, key=rain_ranks.get)[:pool_size]
            candidates |= set(top_rain)

        # RRF scoring
        w_knn = self.alpha
        w_rule = self.rule_weight
        w_rain = self.rainfall_weight if rain_active else 0.0
        w_dtw = max(0.0, 1.0 - self.alpha - self.rule_weight - w_rain)

        combined = {}
        for tid in candidates:
            knn_rank = knn_ranks.get(tid, pool_size)
            dtw_rank = dtw_ranks.get(tid, pool_size) if dtw_ranks else pool_size
            rule_rank = rule_ranks.get(tid, len(self._ids))
            rrf_score = (
                w_knn / (self.rrf_k + knn_rank)
                + w_dtw / (self.rrf_k + dtw_rank)
                + w_rule / (self.rrf_k + rule_rank)
            )
            if w_rain > 0:
                rain_rank = rain_ranks.get(tid, len(self._ids))
                rrf_score += w_rain / (self.rrf_k + rain_rank)
            combined[tid] = rrf_score

        sorted_items = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:k]
        result_ids = [item[0] for item in sorted_items]
        result_scores = [item[1] for item in sorted_items]
        max_s = max(result_scores) if result_scores else 1.0
        scores = [s / (max_s + 1e-8) for s in result_scores]
        distances = [1.0 - s for s in scores]

        return SimilarityResult(
            query_id="query",
            similar_ids=result_ids,
            distances=distances,
            scores=scores,
        )
