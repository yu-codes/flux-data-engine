"""
Coastline Similarity — 海岸線範圍內「絕對位置」路徑相似度

動機：
  既有方法（KNN 摘要特徵 / DTW 相對極座標）比的是抽象特徵與相對台灣中心的形狀，
  導致畫面上路徑「看起來一點都不像」。本方法直接比對**絕對經緯度位置**，
  且只在「台灣海岸線向外擴張 n km」的範圍內計算，使結果就是螢幕上最接近的那幾條線。

演算法：
  1. 將每條颱風軌跡裁切到緩衝區內（距海岸線 <= buffer_km 的點）。
  2. 在本地等距 km 平面上，計算查詢路徑與候選路徑的「對稱平均最近點距離」
     （Chamfer 距離）：
        d(Q,C) = 0.5 * ( mean_{q in Q} dist(q, polyline C)
                       + mean_{c in C} dist(c, polyline Q) )
     其中 dist(點, polyline) 為點到折線各線段的最短距離。
  3. 距離越小 → 兩條線在地圖上越貼近 → 越相似。取前 k 條。

此距離以公里為單位、具明確幾何意義（平均偏離 km），
直接反映「視覺上最接近」的目標。
"""

import numpy as np

from .base import SimilarityBase, SimilarityResult
from . import geometry as cl

DEFAULT_BUFFER_KM = 500.0
# 相似度分數尺度：平均偏離達 SCORE_TAU_KM 時分數約降至 1/e。
# 取較小尺度，使「畫面上明顯貼近（數十 km）」對應高相似度。
SCORE_TAU_KM = 150.0


def _point_to_polyline_dist(p: np.ndarray, line: np.ndarray) -> float:
    """點 p (2,) 到折線 line (M,2) 的最短距離（km 平面）。"""
    if len(line) == 1:
        return float(np.hypot(*(p - line[0])))
    a = line[:-1]
    b = line[1:]
    ab = b - a
    ap = p - a
    denom = np.einsum("ij,ij->i", ab, ab)
    t = np.where(denom > 1e-12, np.einsum("ij,ij->i", ap, ab) / np.maximum(denom, 1e-12), 0.0)
    t = np.clip(t, 0.0, 1.0)
    proj = a + t[:, None] * ab
    d = np.hypot(proj[:, 0] - p[0], proj[:, 1] - p[1])
    return float(np.min(d))


def _chamfer_km(q: np.ndarray, c: np.ndarray) -> float:
    """兩條折線的對稱平均最近點距離 (km)。任一為空回傳 inf。"""
    if len(q) == 0 or len(c) == 0:
        return float("inf")
    d_qc = np.mean([_point_to_polyline_dist(p, c) for p in q])
    d_cq = np.mean([_point_to_polyline_dist(p, q) for p in c])
    return 0.5 * (d_qc + d_cq)


def path_offset_km(lons_a, lats_a, lons_b, lats_b, buffer_km: float) -> float:
    """
    兩條颱風路徑在計算範圍內的「平均偏離」(km) — 對稱平均最近點 / Chamfer 距離。

    供前端顯示用的絕對、可解釋指標（兩條不同路徑必 > 0，不會出現 0km）。
    以 clip_mask 取範圍內的點（含「幾乎沒進範圍時取最近數點」的保底），確保結果有限。
    """
    lons_a = np.asarray(lons_a, dtype=float)
    lats_a = np.asarray(lats_a, dtype=float)
    lons_b = np.asarray(lons_b, dtype=float)
    lats_b = np.asarray(lats_b, dtype=float)
    ma = cl.clip_mask(lons_a, lats_a, buffer_km)
    mb = cl.clip_mask(lons_b, lats_b, buffer_km)
    xa, ya = cl.lonlat_to_km(lons_a, lats_a)
    xb, yb = cl.lonlat_to_km(lons_b, lats_b)
    A = np.column_stack([xa, ya])[ma]
    B = np.column_stack([xb, yb])[mb]
    return _chamfer_km(A, B)


class CoastlineSimilarity(SimilarityBase):
    """海岸線範圍內絕對位置路徑相似度。"""

    def __init__(self, buffer_km: float = DEFAULT_BUFFER_KM):
        self.buffer_km = buffer_km
        self._ids: list[str] = []
        self._loader = None
        # 每條颱風的完整軌跡（km 平面）與各點到海岸距離，供任意 buffer 即時裁切
        self._tracks_km: dict[str, np.ndarray] = {}
        self._dist_to_coast: dict[str, np.ndarray] = {}

    def fit(self, feature_dict: dict, **kwargs):
        self._ids = list(feature_dict.keys())
        self._loader = kwargs.get("loader")
        if self._loader is None:
            raise ValueError("CoastlineSimilarity 需要 loader 以取得原始軌跡")

        for tid in self._ids:
            rec = self._loader.get(tid)
            lons = rec.track["longitude"].values.astype(float)
            lats = rec.track["latitude"].values.astype(float)
            x, y = cl.lonlat_to_km(lons, lats)
            self._tracks_km[tid] = np.column_stack([x, y])
            self._dist_to_coast[tid] = cl.distances_to_coast_km(lons, lats)

        print(
            f"✓ Coastline 已擬合 {len(self._ids)} 筆颱風"
            f"（緩衝 {self.buffer_km:.0f}km 內絕對位置比對）"
        )

    def _clip(self, tid: str, buffer_km: float) -> np.ndarray:
        mask = self._dist_to_coast[tid] <= buffer_km
        return self._tracks_km[tid][mask]

    # ---- 即時預測：用原始軌跡 DataFrame 查詢 ----
    def find_similar_by_track(
        self, track_df, k: int = 5, buffer_km: float | None = None
    ) -> SimilarityResult:
        """
        用查詢颱風的原始軌跡找最接近的 k 條歷史路徑。

        Args:
            track_df: 含 latitude / longitude 欄位的 DataFrame
            k: 返回數量
            buffer_km: 本次查詢的緩衝半徑，None 則用 self.buffer_km
        """
        bkm = self.buffer_km if buffer_km is None else buffer_km
        lons = track_df["longitude"].values.astype(float)
        lats = track_df["latitude"].values.astype(float)
        q_km, _ = cl.clip_track_km(lons, lats, bkm)
        if len(q_km) == 0:
            raise ValueError(
                f"查詢路徑未進入台灣海岸線外 {bkm:.0f}km 範圍內，無法以此方法比對"
            )

        scored = []
        for tid in self._ids:
            c_km = self._clip(tid, bkm)
            if len(c_km) == 0:
                continue
            d = _chamfer_km(q_km, c_km)
            if np.isfinite(d):
                scored.append((tid, d))

        scored.sort(key=lambda x: x[1])
        top = scored[:k]
        return self._build_result("query", top)

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        if query_id not in self._tracks_km:
            raise KeyError(f"找不到颱風：{query_id}")
        q_km = self._clip(query_id, self.buffer_km)
        scored = []
        for tid in self._ids:
            if exclude_self and tid == query_id:
                continue
            c_km = self._clip(tid, self.buffer_km)
            d = _chamfer_km(q_km, c_km)
            if np.isfinite(d):
                scored.append((tid, d))
        scored.sort(key=lambda x: x[1])
        return self._build_result(query_id, scored[:k])

    def compute_distance(self, id_a: str, id_b: str) -> float:
        a = self._clip(id_a, self.buffer_km)
        b = self._clip(id_b, self.buffer_km)
        return _chamfer_km(a, b)

    @staticmethod
    def _build_result(query_id: str, scored: list[tuple[str, float]]) -> SimilarityResult:
        """distances 以 km（平均偏離）表示；scores = exp(-d/τ)（絕對相似度，越大越相似）。"""
        ids = [tid for tid, _ in scored]
        dists = [float(d) for _, d in scored]
        scores = [float(np.exp(-d / SCORE_TAU_KM)) for d in dists]
        return SimilarityResult(
            query_id=query_id,
            similar_ids=ids,
            distances=dists,
            scores=scores,
        )
