"""
Combined Similarity — KNN + DTW + Rule-Based with Reciprocal Rank Fusion (RRF)

使用 RRF 將三組排名融合：
1. KNN: 11 維摘要特徵向量 → 歐式距離排名
2. DTW: 極座標時序路徑 → 物理標準化 DTW 距離排名
3. Rule-Based: 規則分類 → 同類颱風優先排名

Pipeline:
  三組排名各自獨立計算，再以 RRF 融合 → 最終 Top-k
"""

import numpy as np
from .base import SimilarityBase, SimilarityResult
from .knn import KNNSimilarity
from .dtw import DTWSimilarity
from .rule_based import classify_typhoon_by_rules


class CombinedSimilarity(SimilarityBase):
    """
    KNN + DTW + Rule-Based Reciprocal Rank Fusion

    score(tid) = w_knn/(rrf_k+rank_knn) + w_dtw/(rrf_k+rank_dtw) + w_rule/(rrf_k+rank_rule)
    """

    def __init__(
        self,
        alpha: float = 0.2,
        rule_weight: float = 0.5,
        feature_weights: np.ndarray | None = None,
        dtw_weights: np.ndarray | None = None,
        pool_size_factor: int = 10,
        rrf_k: int = 60,
    ):
        self.alpha = alpha
        self.rule_weight = rule_weight
        self.pool_size_factor = pool_size_factor
        self.rrf_k = rrf_k
        self.knn = KNNSimilarity(feature_weights=feature_weights)
        self.dtw = DTWSimilarity(dtw_weights=dtw_weights)
        self._ids: list[str] = []
        self._features_dict = {}
        self._rule_categories: dict[str, str] = {}  # tid -> rule-predicted category
        self._loader = None

    def fit(self, feature_dict: dict, **kwargs):
        self._features_dict = feature_dict
        self._ids = list(feature_dict.keys())
        self._loader = kwargs.get("loader")
        self.knn.fit(feature_dict)
        self.dtw.fit(feature_dict)

        # Pre-compute rule-based classification for all typhoons
        if self._loader:
            for tid in self._ids:
                rec = self._loader.get(tid)
                result = classify_typhoon_by_rules(rec.track, rec.landfall_location)
                self._rule_categories[tid] = result["predicted_category"]

        print(
            f"✓ Combined RRF 已擬合 {len(self._ids)} 筆"
            f"（KNN={self.alpha:.1f}, DTW={1-self.alpha-self.rule_weight:.1f}, Rule={self.rule_weight:.1f}）"
        )

    def compute_distance(self, id_a: str, id_b: str) -> float:
        knn_d = self.knn.compute_distance(id_a, id_b)
        dtw_d = self.dtw.compute_distance(id_a, id_b)
        return self.alpha * knn_d + (1 - self.alpha) * dtw_d

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

        # Merge candidates (include same-category typhoons from rule-based)
        candidates = set(knn_result.similar_ids + dtw_result.similar_ids)
        if self._rule_categories:
            # Add top same-category typhoons to candidate pool
            same_cat_ids_set = set(same_cat_ids[:pool_size])
            candidates |= same_cat_ids_set
        if exclude_self:
            candidates.discard(query_id)

        # RRF scoring with three components
        w_knn = self.alpha
        w_rule = self.rule_weight
        w_dtw = max(0.0, 1.0 - self.alpha - self.rule_weight)

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
            combined[tid] = rrf_score

        # Sort descending (higher = more similar)
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
        }

    def find_similar_by_vector(
        self, query_vec: np.ndarray, k: int = 5, query_features=None
    ) -> SimilarityResult:
        """
        用特徵向量查詢（支援即時預測的新颱風）

        Args:
            query_vec: 11 維特徵向量（用於 KNN）
            k: 返回前 k 個
            query_features: TyphoonFeatures 物件（用於 DTW，可選）
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

        # 3. Rule-Based ranking（若已預計算分類）
        # 對新颱風，用 KNN top-1 的分類做 proxy
        rule_ranks = {}
        if self._rule_categories and knn_result.similar_ids:
            # 用 KNN 最相似的颱風的 rule 分類做 proxy
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

        # Merge candidates
        candidates = set(knn_result.similar_ids)
        if dtw_ranks:
            candidates |= set(list(dtw_ranks.keys())[:pool_size])
        if rule_ranks:
            same_cat_set = set(
                t for t in self._ids if rule_ranks.get(t, len(self._ids)) < pool_size
            )
            candidates |= same_cat_set

        # RRF scoring
        w_knn = self.alpha
        w_rule = self.rule_weight
        w_dtw = max(0.0, 1.0 - self.alpha - self.rule_weight)

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
