"""
Baseline 相似度 — 隨機分類 (作為下限基準)

策略：對每個查詢颱風，隨機選擇 k 個歷史颱風作為「相似」。
這是最粗糙的方法，用來確認其他算法是否真正學到了東西。
"""

import numpy as np
from .base import SimilarityBase, SimilarityResult


class BaselineSimilarity(SimilarityBase):
    """
    隨機基線：隨機挑選 k 個颱風。

    用於驗證其他方法是否真的優於隨機。
    """

    def __init__(self, seed: int = 42):
        self._ids: list[str] = []
        self._features_dict = {}
        self._rng = np.random.RandomState(seed)

    def fit(self, feature_dict: dict):
        self._features_dict = feature_dict
        self._ids = list(feature_dict.keys())
        print(f"✓ Baseline（隨機）已擬合 {len(self._ids)} 筆颱風")

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        candidates = (
            [tid for tid in self._ids if tid != query_id]
            if exclude_self
            else list(self._ids)
        )
        chosen = self._rng.choice(
            candidates, size=min(k, len(candidates)), replace=False
        )

        # 距離隨機
        dists = self._rng.uniform(0.5, 5.0, size=len(chosen)).tolist()
        scores = [1.0 / (d + 1e-8) for d in dists]

        return SimilarityResult(
            query_id=query_id,
            similar_ids=list(chosen),
            distances=dists,
            scores=scores,
        )

    def compute_distance(self, id_a: str, id_b: str) -> float:
        return self._rng.uniform(0.5, 5.0)
