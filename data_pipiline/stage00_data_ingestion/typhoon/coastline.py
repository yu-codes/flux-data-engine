"""
台灣海岸線與外擴緩衝區（buffer）幾何工具

用途：
1. 提供台灣本島海岸線輪廓（簡化多邊形，lon/lat）
2. 將海岸線向外擴張 n km，產生緩衝區多邊形（供前端投影繪製）
3. 判斷某座標是否落在緩衝區內、距海岸線多遠（供「絕對位置相似度」只在範圍內計算）

設計重點（無 shapely / geopandas 依賴，純 numpy）：
- 以台灣中心為原點，將 lon/lat 投影到「本地等距 km 平面」(x_km, y_km)，
  在此平面上做距離 / 緩衝計算，避免經緯度不等距問題。
- 海岸線外擴緩衝 = 海岸線凸包 ⊕ 半徑 n km 圓盤（Minkowski sum），
  邊外推 + 頂點圓弧 → 平滑且不自交，適合大半徑（如 500km）視覺呈現。
- 點到海岸線距離 = 點到輪廓多邊形各邊的最短距離；落在島內回傳 0。
"""

import numpy as np

# 本地投影參考點（台灣中心）
REF_LAT = 23.7
REF_LON = 121.0

# 每度約略公里數
KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON = 111.32  # 乘上 cos(lat) 修正


# ---------------------------------------------------------------------------
# 台灣本島海岸線輪廓（順時針，(lon, lat)）— 簡化版約 25 點
# 從北端富貴角起，沿東岸南下至鵝鑾鼻，再沿西岸北上回到起點。
# 解析度足以做投影繪製與緩衝計算（非精密測量用途）。
# ---------------------------------------------------------------------------
TAIWAN_OUTLINE_LONLAT: list[tuple[float, float]] = [
    (121.53, 25.30),  # 富貴角（北端）
    (121.69, 25.16),  # 基隆
    (122.00, 25.01),  # 三貂角（東北角）
    (121.86, 24.85),
    (121.85, 24.60),  # 宜蘭海岸
    (121.75, 24.13),  # 蘇澳/東澳
    (121.55, 23.78),  # 花蓮
    (121.50, 23.40),  # 秀姑巒溪口
    (121.40, 23.10),  # 成功
    (121.20, 22.80),  # 台東
    (121.00, 22.45),  # 大武
    (120.90, 22.00),  # 旭海
    (120.85, 21.90),  # 鵝鑾鼻（南端）
    (120.74, 21.93),  # 貓鼻頭（西南）
    (120.55, 22.30),  # 枋寮
    (120.30, 22.55),  # 高雄外海
    (120.15, 22.95),  # 台南安平
    (120.10, 23.30),  # 嘉義外傘頂
    (120.13, 23.70),  # 雲林麥寮
    (120.50, 24.30),  # 台中大肚溪口
    (120.78, 24.65),  # 苗栗通霄
    (121.00, 25.00),  # 新竹/桃園海岸
    (121.25, 25.13),  # 林口/淡水西
    (121.40, 25.28),  # 北海岸
]


# ---------------------------------------------------------------------------
# 投影：lon/lat <-> 本地 km 平面
# ---------------------------------------------------------------------------
_COS_REF = np.cos(np.radians(REF_LAT))


def lonlat_to_km(lon, lat):
    """經緯度 → 本地等距 km 平面 (x_km, y_km)。可接受純量或 ndarray。"""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    x = (lon - REF_LON) * KM_PER_DEG_LON * _COS_REF
    y = (lat - REF_LAT) * KM_PER_DEG_LAT
    return x, y


def km_to_lonlat(x, y):
    """本地 km 平面 → 經緯度 (lon, lat)。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    lon = REF_LON + x / (KM_PER_DEG_LON * _COS_REF)
    lat = REF_LAT + y / KM_PER_DEG_LAT
    return lon, lat


def _outline_km() -> np.ndarray:
    """海岸線輪廓於 km 平面的座標 (N,2)。"""
    pts = np.array(TAIWAN_OUTLINE_LONLAT, dtype=float)
    x, y = lonlat_to_km(pts[:, 0], pts[:, 1])
    return np.column_stack([x, y])


# ---------------------------------------------------------------------------
# 點到多邊形 / 線段距離（km 平面）
# ---------------------------------------------------------------------------
def _point_to_segment_dist(px, py, ax, ay, bx, by) -> float:
    """點 (px,py) 到線段 AB 的最短距離（向量化於單點）。"""
    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    denom = abx * abx + aby * aby
    if denom <= 1e-12:
        return float(np.hypot(apx, apy))
    t = (apx * abx + apy * aby) / denom
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * abx, ay + t * aby
    return float(np.hypot(px - cx, py - cy))


def _point_in_polygon(px, py, poly: np.ndarray) -> bool:
    """射線法判斷點是否在多邊形內（km 平面）。"""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi
        ):
            inside = not inside
        j = i
    return inside


def distance_to_coast_km_single(lon: float, lat: float, poly_km: np.ndarray) -> float:
    """單點到海岸線的距離 (km)，落在島內回傳 0。"""
    x, y = lonlat_to_km(lon, lat)
    x, y = float(x), float(y)
    if _point_in_polygon(x, y, poly_km):
        return 0.0
    n = len(poly_km)
    best = float("inf")
    for i in range(n):
        ax, ay = poly_km[i]
        bx, by = poly_km[(i + 1) % n]
        d = _point_to_segment_dist(x, y, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def _points_in_polygon(px: np.ndarray, py: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """射線法（向量化於點，迴圈於多邊形邊）。回傳 (N,) bool。"""
    n = len(poly)
    inside = np.zeros(len(px), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cond = ((yi > py) != (yj > py)) & (
            px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi
        )
        inside ^= cond
        j = i
    return inside


def _points_to_polyline_min(px: np.ndarray, py: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """各點到封閉多邊形所有邊的最短距離 (km)，向量化。回傳 (N,)。"""
    A = poly
    B = np.roll(poly, -1, axis=0)  # 封閉：最後一點接回第一點
    AB = B - A  # (M,2)
    denom = np.maximum(np.einsum("md,md->m", AB, AB), 1e-12)  # (M,)
    P = np.column_stack([px, py])  # (N,2)
    AP = P[:, None, :] - A[None, :, :]  # (N,M,2)
    t = np.clip(np.einsum("nmd,md->nm", AP, AB) / denom, 0.0, 1.0)  # (N,M)
    proj = A[None, :, :] + t[:, :, None] * AB[None, :, :]  # (N,M,2)
    d = np.hypot(proj[:, :, 0] - px[:, None], proj[:, :, 1] - py[:, None])  # (N,M)
    return d.min(axis=1)


def distances_to_coast_km(lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    """一組座標各自到海岸線的距離 (km)，島內回傳 0（向量化）。"""
    poly_km = _outline_km()
    x, y = lonlat_to_km(lons, lats)
    x = np.atleast_1d(x).astype(float)
    y = np.atleast_1d(y).astype(float)
    d = _points_to_polyline_min(x, y, poly_km)
    d[_points_in_polygon(x, y, poly_km)] = 0.0
    return d


# ---------------------------------------------------------------------------
# 凸包（Andrew's monotone chain）+ Minkowski 圓盤 → 緩衝多邊形
# ---------------------------------------------------------------------------
def _convex_hull(points: np.ndarray) -> np.ndarray:
    """回傳凸包頂點 (CCW)，km 平面。"""
    pts = sorted(map(tuple, points))
    pts = np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = np.array(lower[:-1] + upper[:-1])
    return hull  # CCW


def buffer_polygon(buffer_km: float, arc_steps: int = 6) -> list[dict]:
    """
    海岸線向外擴張 buffer_km，回傳緩衝區多邊形 [{"lat":.., "lon":..}, ...]。

    作法：海岸線凸包 ⊕ 半徑 buffer_km 圓盤（邊外推 + 頂點圓弧）。
    凸包用於確保大半徑緩衝平滑不自交；點到海岸距離判斷仍以實際輪廓為準。
    """
    hull = _convex_hull(_outline_km())  # CCW, (N,2)
    n = len(hull)
    # 每條邊的外向單位法向量（CCW 多邊形：外向 = 邊方向順時針旋轉 90°）
    normals = np.zeros((n, 2))
    for i in range(n):
        a = hull[i]
        b = hull[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        ln = np.hypot(ex, ey) + 1e-12
        normals[i] = (ey / ln, -ex / ln)  # 外向

    out: list[tuple[float, float]] = []
    for i in range(n):
        a = hull[i]
        ni = normals[i]
        nip = normals[(i - 1) % n]  # 前一條邊的法向量
        # 在頂點 a 插入由 nip → ni 的外向圓弧
        ang0 = np.arctan2(nip[1], nip[0])
        ang1 = np.arctan2(ni[1], ni[0])
        # 確保 CCW 方向（角度遞增）
        if ang1 < ang0:
            ang1 += 2 * np.pi
        steps = max(1, int(np.ceil((ang1 - ang0) / (np.pi / 2 / arc_steps))))
        for s in range(steps + 1):
            ang = ang0 + (ang1 - ang0) * s / steps
            out.append((a[0] + buffer_km * np.cos(ang),
                        a[1] + buffer_km * np.sin(ang)))
        # 邊 a->b 外推後的終點
        b = hull[(i + 1) % n]
        out.append((b[0] + buffer_km * ni[0], b[1] + buffer_km * ni[1]))

    out_arr = np.array(out)
    lon, lat = km_to_lonlat(out_arr[:, 0], out_arr[:, 1])
    return [{"lat": round(float(la), 4), "lon": round(float(lo), 4)}
            for lo, la in zip(lon, lat)]


def outline_lonlat() -> list[dict]:
    """海岸線輪廓 [{"lat":.., "lon":..}, ...]（閉合，供前端繪製）。"""
    ring = TAIWAN_OUTLINE_LONLAT + [TAIWAN_OUTLINE_LONLAT[0]]
    return [{"lat": la, "lon": lo} for lo, la in ring]


# ---------------------------------------------------------------------------
# 軌跡裁切：取出落在緩衝區內（距海岸 <= buffer_km）的點
# ---------------------------------------------------------------------------
def clip_mask(
    lons: np.ndarray, lats: np.ndarray, buffer_km: float, min_points: int = 2
) -> np.ndarray:
    """
    回傳「落在緩衝區內（距海岸 <= buffer_km）」的布林遮罩 (N,)。

    若範圍內不足 min_points 個點（颱風幾乎沒進入範圍），
    退而取距海岸最近的數個點，確保下游運算仍有足夠資料。
    """
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    d = distances_to_coast_km(lons, lats)
    mask = d <= buffer_km
    if mask.sum() < min_points:
        n = max(min_points, len(d) // 5)
        idx = np.argsort(d)[:n]
        mask = np.zeros(len(d), dtype=bool)
        mask[idx] = True
    return mask


def clip_track_km(
    lons: np.ndarray, lats: np.ndarray, buffer_km: float
) -> tuple[np.ndarray, np.ndarray]:
    """
    將軌跡裁切到緩衝區內。

    Returns:
        (km_points, mask)
        km_points: 落在範圍內的點於 km 平面座標 (M,2)
        mask: 原軌跡各點是否在範圍內 (N,)
    """
    dists = distances_to_coast_km(lons, lats)
    mask = dists <= buffer_km
    x, y = lonlat_to_km(lons, lats)
    km_pts = np.column_stack([x, y])[mask]
    return km_pts, mask
