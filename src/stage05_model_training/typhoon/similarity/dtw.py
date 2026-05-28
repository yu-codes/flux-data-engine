"""
DTW 相似度 v2 — 基於 impact window 時序路徑的 Dynamic Time Warping

改進：
1. 環形方位角距離：min(|θ1-θ2|, 2π-|θ1-θ2|)
2. 特徵標準化：Δr/300, Δθ/π, Δwind/100, Δpressure/50
3. Sakoe-Chiba band 限制（防止慢颱風被對齊為快颱風）
4. 距離加權：靠近台灣的點權重更高
"""

import numpy as np
from .base import SimilarityBase, SimilarityResult

# 標準化常數
NORM_R = 300.0  # km
NORM_THETA = np.pi  # radians
NORM_WIND = 100.0  # kt
NORM_PRESSURE = 50.0  # mb

# Sakoe-Chiba band 寬度（比例）
SAKOE_CHIBA_RATIO = 0.3  # 允許 30% 的時間偏移


def _circular_distance(theta1: float, theta2: float) -> float:
    """環形方位角距離（處理 -π 到 π 跨越問題）"""
    diff = abs(theta1 - theta2)
    return min(diff, 2 * np.pi - diff)


def _dtw_distance(
    seq1: np.ndarray,
    seq2: np.ndarray,
    weights: np.ndarray | None = None,
    use_sakoe_chiba: bool = True,
) -> float:
    """
    計算兩個多維時序的 DTW 距離（v2）

    改進：
    - 方位角使用環形距離
    - 特徵已預先標準化
    - Sakoe-Chiba band 限制時間彎曲

    Args:
        seq1: (T1, D) 時序矩陣（已標準化）
        seq2: (T2, D) 時序矩陣（已標準化）
        weights: (D,) 各維度的權重
        use_sakoe_chiba: 是否使用 Sakoe-Chiba band

    Returns:
        DTW 距離
    """
    n, m = len(seq1), len(seq2)
    if n == 0 or m == 0:
        return float("inf")

    if weights is None:
        weights = np.ones(seq1.shape[1])

    # 標準化權重
    weights = weights / np.sum(weights)

    # Sakoe-Chiba band
    if use_sakoe_chiba:
        band = max(1, int(SAKOE_CHIBA_RATIO * max(n, m)))
    else:
        band = max(n, m)

    # 計算成本矩陣
    cost = np.full((n + 1, m + 1), float("inf"))
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        # Sakoe-Chiba band 限制
        j_start = max(1, i - band)
        j_end = min(m, i + band)
        for j in range(j_start, j_end + 1):
            # 逐維計算距離
            d = 0.0
            for dim in range(seq1.shape[1]):
                if dim == 1:  # theta 維度用環形距離
                    d += (
                        weights[dim]
                        * _circular_distance(seq1[i - 1, dim], seq2[j - 1, dim]) ** 2
                    )
                else:
                    d += weights[dim] * (seq1[i - 1, dim] - seq2[j - 1, dim]) ** 2

            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    # 歸一化（除以路徑長度）
    path_len = n + m
    return float(np.sqrt(cost[n, m] / path_len))


class DTWSimilarity(SimilarityBase):
    """
    DTW 時序路徑相似度 v2

    改進：
    - 環形方位角距離
    - 特徵標準化（物理意義）
    - Sakoe-Chiba band
    - 距離加權（靠近台灣的點權重高）
    """

    def __init__(self, dtw_weights: np.ndarray | None = None):
        """
        Args:
            dtw_weights: DTW 各維度權重 [w_r, w_theta, w_wind, w_pressure]
        """
        if dtw_weights is None:
            self.dtw_weights = np.array([1.0, 1.0, 1.0, 0.5])
        else:
            self.dtw_weights = np.array(dtw_weights)

        self._features_dict = {}
        self._ids: list[str] = []
        self._matrices: dict[str, np.ndarray] = {}
        self._distance_cache: dict[tuple, float] = {}

    def fit(self, feature_dict: dict):
        self._features_dict = feature_dict
        self._ids = list(feature_dict.keys())
        self._distance_cache = {}

        # 物理意義標準化（不是統計標準化）
        for tid in self._ids:
            mat = feature_dict[tid].get_impact_window_matrix()
            # 標準化：[r/300, theta/π, wind/100, pressure/50]
            normalized = mat.copy()
            normalized[:, 0] /= NORM_R  # r: 0~300 → 0~1
            normalized[:, 1] /= NORM_THETA  # theta: -π~π → -1~1
            normalized[:, 2] /= NORM_WIND  # wind: 0~100 → 0~1
            normalized[:, 3] = (
                normalized[:, 3] - 950
            ) / NORM_PRESSURE  # pressure centered

            # 距離加權：exp(-r_orig / 200) 讓靠近台灣的點權重更高
            r_orig = mat[:, 0]
            distance_weight = np.exp(-r_orig / 200.0)
            # 應用權重到所有維度
            for dim in range(normalized.shape[1]):
                normalized[:, dim] *= distance_weight

            self._matrices[tid] = normalized

        print(
            f"✓ DTW v2 已擬合 {len(self._ids)} 筆颱風（物理標準化 + 環形距離 + Sakoe-Chiba）"
        )

    @staticmethod
    def normalize_matrix(mat: np.ndarray) -> np.ndarray:
        """將 impact window 矩陣標準化（與 fit 中相同邏輯）"""
        normalized = mat.copy()
        normalized[:, 0] /= NORM_R
        normalized[:, 1] /= NORM_THETA
        normalized[:, 2] /= NORM_WIND
        normalized[:, 3] = (normalized[:, 3] - 950) / NORM_PRESSURE
        r_orig = mat[:, 0]
        distance_weight = np.exp(-r_orig / 200.0)
        for dim in range(normalized.shape[1]):
            normalized[:, dim] *= distance_weight
        return normalized

    def find_similar_by_matrix(
        self, query_mat: np.ndarray, k: int = 5
    ) -> SimilarityResult:
        """用標準化後的矩陣查詢相似颱風"""
        query_norm = self.normalize_matrix(query_mat)
        distances = {}
        for tid in self._ids:
            distances[tid] = _dtw_distance(
                query_norm, self._matrices[tid], self.dtw_weights, use_sakoe_chiba=True
            )
        sorted_items = sorted(distances.items(), key=lambda x: x[1])[:k]
        result_ids = [item[0] for item in sorted_items]
        result_dists = [item[1] for item in sorted_items]
        max_d = max(result_dists) if result_dists else 1.0
        scores = [1.0 - d / (max_d + 1e-8) for d in result_dists]
        return SimilarityResult(
            query_id="query",
            similar_ids=result_ids,
            distances=result_dists,
            scores=scores,
        )

    def compute_distance(self, id_a: str, id_b: str) -> float:
        key = tuple(sorted([id_a, id_b]))
        if key not in self._distance_cache:
            mat_a = self._matrices[id_a]
            mat_b = self._matrices[id_b]
            dist = _dtw_distance(mat_a, mat_b, self.dtw_weights, use_sakoe_chiba=True)
            self._distance_cache[key] = dist
        return self._distance_cache[key]

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        if query_id not in self._matrices:
            raise KeyError(f"找不到颱風：{query_id}")

        distances = {}
        for tid in self._ids:
            if exclude_self and tid == query_id:
                continue
            distances[tid] = self.compute_distance(query_id, tid)

        sorted_items = sorted(distances.items(), key=lambda x: x[1])[:k]

        result_ids = [item[0] for item in sorted_items]
        result_dists = [item[1] for item in sorted_items]
        max_d = max(result_dists) if result_dists else 1.0
        scores = [1.0 - d / (max_d + 1e-8) for d in result_dists]

        return SimilarityResult(
            query_id=query_id,
            similar_ids=result_ids,
            distances=result_dists,
            scores=scores,
        )
