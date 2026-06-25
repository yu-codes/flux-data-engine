"""
Coastline RRF — 以「絕對位置相似度」為主，融合 KNN 與（可選）降水的多排名投票

動機：
  coastline（海岸線範圍內絕對位置 / Chamfer 距離）能找出地圖上最貼近的路徑，
  但只看幾何。本方法在其基礎上，用 Reciprocal Rank Fusion (RRF) 再納入：
    1. Coastline 排名（絕對位置，主訊號，占極高比重）
    2. KNN 排名（11 維摘要特徵歐式距離）
    3. Rainfall 排名（可選，指定地區事件降水相近優先）

  → 三組候選排名融合成最終 Top-k，再交給類比投票預測分類。

RRF 融合：
  score(tid) = w_coast/(rrf_k+rank_coast)
             + w_knn  /(rrf_k+rank_knn)
             + w_rain /(rrf_k+rank_rain)   # 可選

  其中 w_coast 占主導（預設 0.72），確保「絕對位置最接近」主導結果。
  所有計算只在「海岸線外擴 buffer_km」範圍內進行（與 coastline / 特徵擷取一致）。
"""

import numpy as np

from .base import SimilarityBase, SimilarityResult
from .coastline import CoastlineSimilarity
from .knn import KNNSimilarity
from data_pipiline.stage00_data_ingestion.typhoon.regions import DEFAULT_REGION


class CoastlineRRFSimilarity(SimilarityBase):
    """絕對位置（主）+ KNN + 降水（可選）的 RRF 融合相似度。"""

    def __init__(
        self,
        buffer_km: float = 500.0,
        w_coastline: float = 0.80,
        w_knn: float = 0.20,
        w_rainfall: float = 0.08,
        feature_weights: np.ndarray | None = None,
        pool_size_factor: int = 12,
        rrf_k: int = 60,
        use_rainfall: bool = False,
        rainfall_region: str = DEFAULT_REGION,
    ):
        self.buffer_km = buffer_km
        self.w_coastline = w_coastline
        self.w_knn = w_knn
        self.use_rainfall = use_rainfall
        self.w_rainfall = w_rainfall if use_rainfall else 0.0
        self.rainfall_region = rainfall_region
        self.pool_size_factor = pool_size_factor
        self.rrf_k = rrf_k

        self.coastline = CoastlineSimilarity(buffer_km=buffer_km)
        self.knn = KNNSimilarity(feature_weights=feature_weights)
        self._ids: list[str] = []
        self._rainfall: dict[str, float | None] = {}
        self._loader = None

    def fit(self, feature_dict: dict, **kwargs):
        self._ids = list(feature_dict.keys())
        self._loader = kwargs.get("loader")
        if self._loader is None:
            raise ValueError("CoastlineRRFSimilarity 需要 loader 以取得原始軌跡")
        self.coastline.fit(feature_dict, loader=self._loader)
        self.knn.fit(feature_dict)
        if self.use_rainfall:
            for tid in self._ids:
                self._rainfall[tid] = self._loader.get(tid).get_rainfall(
                    self.rainfall_region
                )
        rain_msg = (
            f", Rain[{self.rainfall_region}]={self.w_rainfall:.2f}"
            if self.use_rainfall
            else ""
        )
        print(
            f"✓ Coastline-RRF 已擬合 {len(self._ids)} 筆"
            f"（Coast={self.w_coastline:.2f}, KNN={self.w_knn:.2f}{rain_msg}, "
            f"緩衝 {self.buffer_km:.0f}km）"
        )

    def configure_rainfall(
        self, use_rainfall: bool, region: str | None = None, weight: float | None = None
    ):
        """執行期切換降水訊號（供 backend 逐請求調整）"""
        self.use_rainfall = use_rainfall
        if region is not None:
            self.rainfall_region = region
        if weight is not None:
            self.w_rainfall = weight if use_rainfall else 0.0
        else:
            self.w_rainfall = self.w_rainfall if use_rainfall else 0.0
        if use_rainfall and self._loader:
            self._rainfall = {
                tid: self._loader.get(tid).get_rainfall(self.rainfall_region)
                for tid in self._ids
            }

    def _rainfall_ranks(self, query_rain: float | None) -> dict[str, int]:
        """依與 query 降水量的差距排名（差距小→排前），無資料者排最後"""
        if query_rain is None or not self._rainfall:
            return {}
        scored, missing = [], []
        for tid in self._ids:
            r = self._rainfall.get(tid)
            (missing if r is None else scored).append(
                tid if r is None else (tid, abs(query_rain - r))
            )
        scored.sort(key=lambda x: x[1])
        ranks = {tid: rank for rank, (tid, _) in enumerate(scored)}
        for rank, tid in enumerate(missing, len(scored)):
            ranks[tid] = rank
        return ranks

    def _fuse(
        self,
        query_id: str,
        coast_ids: list[str],
        knn_ids: list[str],
        rain_ranks: dict[str, int],
        k: int,
        pool_size: int,
    ) -> SimilarityResult:
        coast_ranks = {tid: rank for rank, tid in enumerate(coast_ids)}
        knn_ranks = {tid: rank for rank, tid in enumerate(knn_ids)}

        candidates = set(coast_ids) | set(knn_ids)
        rain_active = self.w_rainfall > 0 and bool(rain_ranks)
        if rain_active:
            candidates |= set(sorted(rain_ranks, key=rain_ranks.get)[:pool_size])
        candidates.discard(query_id)

        combined = {}
        for tid in candidates:
            c_rank = coast_ranks.get(tid, pool_size)
            k_rank = knn_ranks.get(tid, pool_size)
            score = (
                self.w_coastline / (self.rrf_k + c_rank)
                + self.w_knn / (self.rrf_k + k_rank)
            )
            if rain_active:
                score += self.w_rainfall / (self.rrf_k + rain_ranks.get(tid, len(self._ids)))
            combined[tid] = score

        top = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:k]
        ids = [t for t, _ in top]
        raw = [s for _, s in top]
        max_s = max(raw) if raw else 1.0
        scores = [s / (max_s + 1e-8) for s in raw]
        distances = [1.0 - s for s in scores]
        return SimilarityResult(
            query_id=query_id, similar_ids=ids, distances=distances, scores=scores
        )

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        pool = min(len(self._ids) - 1, k * self.pool_size_factor)
        coast = self.coastline.find_similar(query_id, pool, exclude_self)
        knn = self.knn.find_similar(query_id, pool, exclude_self)
        rain_ranks = (
            self._rainfall_ranks(self._rainfall.get(query_id))
            if self.w_rainfall > 0
            else {}
        )
        return self._fuse(query_id, coast.similar_ids, knn.similar_ids, rain_ranks, k, pool)

    def find_similar_by_track(
        self,
        track_df,
        query_vec: np.ndarray,
        k: int = 5,
        buffer_km: float | None = None,
        query_rainfall: float | None = None,
    ) -> SimilarityResult:
        """即時預測：用原始軌跡（coastline）+ 特徵向量（KNN）+ 可選降水查詢。"""
        pool = min(len(self._ids), k * self.pool_size_factor)
        coast = self.coastline.find_similar_by_track(track_df, k=pool, buffer_km=buffer_km)
        knn = self.knn.find_similar_by_vector(query_vec, k=pool)
        rain_ranks = (
            self._rainfall_ranks(query_rainfall)
            if (self.w_rainfall > 0 and query_rainfall is not None)
            else {}
        )
        return self._fuse("query", coast.similar_ids, knn.similar_ids, rain_ranks, k, pool)

    def compute_distance(self, id_a: str, id_b: str) -> float:
        # 以絕對位置（coastline）距離為代表
        return self.coastline.compute_distance(id_a, id_b)
