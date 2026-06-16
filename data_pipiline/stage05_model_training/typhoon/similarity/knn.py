"""
KNN 相似度 — 基於摘要特徵向量的歐式距離
"""

import numpy as np
from sklearn.preprocessing import StandardScaler
from .base import SimilarityBase, SimilarityResult


class KNNSimilarity(SimilarityBase):
    """
    基於摘要特徵的 KNN 相似度

    使用標準化後的歐式距離
    """

    def __init__(self, feature_weights: np.ndarray | None = None):
        """
        Args:
            feature_weights: 各特徵的權重（11維），None 時等權
        """
        self.feature_weights = feature_weights
        self.scaler = StandardScaler()
        self._ids: list[str] = []
        self._vectors: np.ndarray | None = None  # (N, D) 標準化後
        self._features_dict = {}

    def fit(self, feature_dict: dict):
        self._features_dict = feature_dict
        self._ids = list(feature_dict.keys())

        raw_vectors = np.array(
            [feature_dict[tid].to_feature_vector() for tid in self._ids]
        )
        self._vectors = self.scaler.fit_transform(raw_vectors)

        if self.feature_weights is not None:
            w = np.array(self.feature_weights, dtype=np.float64)
            self._vectors = self._vectors * w

        print(f"✓ KNN 已擬合 {len(self._ids)} 筆颱風（{raw_vectors.shape[1]} 維特徵）")

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        if query_id not in self._features_dict:
            raise KeyError(f"找不到颱風：{query_id}")

        idx = self._ids.index(query_id)
        query_vec = self._vectors[idx]

        distances = np.linalg.norm(self._vectors - query_vec, axis=1)

        sorted_indices = np.argsort(distances)

        result_ids = []
        result_dists = []
        for i in sorted_indices:
            if exclude_self and self._ids[i] == query_id:
                continue
            result_ids.append(self._ids[i])
            result_dists.append(float(distances[i]))
            if len(result_ids) >= k:
                break

        # 分數：距離越小越相似
        max_d = max(result_dists) if result_dists else 1.0
        scores = [1.0 - d / (max_d + 1e-8) for d in result_dists]

        return SimilarityResult(
            query_id=query_id,
            similar_ids=result_ids,
            distances=result_dists,
            scores=scores,
        )

    def compute_distance(self, id_a: str, id_b: str) -> float:
        idx_a = self._ids.index(id_a)
        idx_b = self._ids.index(id_b)
        return float(np.linalg.norm(self._vectors[idx_a] - self._vectors[idx_b]))

    def transform_query(self, feature_vector: np.ndarray) -> np.ndarray:
        """將原始特徵向量轉換為標準化後的向量（用於外部查詢）"""
        scaled = self.scaler.transform(feature_vector.reshape(1, -1))
        if self.feature_weights is not None:
            scaled = scaled * np.array(self.feature_weights)
        return scaled.flatten()

    def find_similar_by_vector(
        self, query_vector: np.ndarray, k: int = 5
    ) -> SimilarityResult:
        """用原始特徵向量查詢"""
        scaled = self.transform_query(query_vector)
        distances = np.linalg.norm(self._vectors - scaled, axis=1)
        sorted_indices = np.argsort(distances)[:k]

        result_ids = [self._ids[i] for i in sorted_indices]
        result_dists = [float(distances[i]) for i in sorted_indices]
        max_d = max(result_dists) if result_dists else 1.0
        scores = [1.0 - d / (max_d + 1e-8) for d in result_dists]

        return SimilarityResult(
            query_id="query",
            similar_ids=result_ids,
            distances=result_dists,
            scores=scores,
        )
