"""
規則式路徑分類 — 基於 CWA 官方分類定義（1-9 類）

根據颱風路徑相對於台灣的幾何特徵判斷侵臺路徑分類：
  1: 通過台灣北部海面向西或西北西進行者
  2: 通過台灣北部向西或西北進行者（含登陸北部）
  3: 通過台灣中部向西進行者（含登陸中部）
  4: 通過台灣南部向西進行者（含登陸南部）
  5: 通過台灣南部海面向西進行者
  6: 沿台灣東岸或東部海面北上者
  7: 通過台灣南部海面向東或東北進行者
  8: 通過台灣南部海面向北或北北西進行者
  9: 西北太平洋或南海生成後對台灣無侵襲，但有影響者（含特殊路徑）

分類策略（優先順序）:
  1) Cat 6: 東側北行、不穿越西側（即使有登陸也優先判 6）
  2) Cat 8: 南方入、東北出、不穿越、遠離台灣南方
  3) 有登陸文字 → 精確解析地名 → Cat 2/3/4/7/9
  4) Cat 7: 南方入、西側北上
  5) Cat 1/5: 海面通過
  6) Cat 9: 不規則路徑
"""

import numpy as np
import pandas as pd
from data_pipiline.stage04_feature_engineering.typhoon.extractor import (
    haversine,
    haversine_vec,
    TAIWAN_LAT,
    TAIWAN_LON,
)
from .base import SimilarityBase, SimilarityResult

# === 台灣地理參數 ===
TAIWAN_CENTER_LAT = 23.5
TAIWAN_CENTER_LON = 121.0
TAIWAN_EAST_LON = 121.8
TAIWAN_WEST_LON = 120.2

# 距離閾值
CONTEXT_RADIUS_KM = 500
LANDFALL_KM = 100
WEST_CROSSING_LON = 120.3


# === 地名分類 ===
EAST_NORTH_KEYWORDS = [
    "基隆",
    "宜蘭",
    "彭佳嶼",
    "蘇澳",
    "頭城",
    "三貂角",
    "新北",
    "淡水",
    "南澳",
    "蘭陽",
    "秀林",
]
EAST_CENTRAL_KEYWORDS = [
    "花蓮",
    "新港",
    "成功",
    "秀姑巒",
    "豐濱",
    "長濱",
    "靜浦",
    "東澳",
    "東河",
]
EAST_SOUTH_KEYWORDS = [
    "臺東",
    "台東",
    "大武",
    "太麻里",
    "滿州",
    "鵝鑾鼻",
]
WEST_SOUTH_KEYWORDS = [
    "金門",
    "高雄",
    "小港",
    "東石",
    "台中",
    "臺中",
    "苗栗",
    "彰化",
    "雲林",
    "嘉義",
]
SOUTH_TIP_KEYWORDS = [
    "恆春",
    "屏東",
    "楓港",
    "枋寮",
]


def _parse_landfall_detail(landfall_location: str) -> str | None:
    """
    解析登陸地點 → 分類區域

    CWA 官方分類的地理邊界（從北到南）：
    - Cat 2 (北部): 基隆/三貂角 ~ 宜蘭 ~ 花蓮（含「宜蘭至花蓮間」）
    - Cat 3 (中部): 花蓮 ~ 成功/新港（含「花蓮至成功間」）
    - Cat 4 (南部): 成功/台東 以南
    """
    if not landfall_location:
        return None
    loc = str(landfall_location).strip()
    if loc in ("", "---", "nan", "None", "無登陸"):
        return None

    # 1. 處理複合地名的關鍵邊界情況
    has_hualien = "花蓮" in loc
    has_yilan = "宜蘭" in loc
    has_taitung = "臺東" in loc or "台東" in loc
    has_chenggong = "成功" in loc or "新港" in loc
    has_fengbin = "豐濱" in loc or "長濱" in loc or "靜浦" in loc

    # 「宜蘭至花蓮間」→ east_north (Cat 2) — CWA 官方定義
    if has_yilan and has_hualien:
        return "east_north"

    # 「宜蘭」相關（不含花蓮）→ east_north (Cat 2)
    if has_yilan:
        return "east_north"

    # 「花蓮至成功/新港間」→ east_central (Cat 3)
    if has_hualien and has_chenggong:
        return "east_central"

    # 「花蓮及臺東交界」→ east_central (Cat 3，偏中段)
    if has_hualien and has_taitung:
        return "east_central"

    # 「花蓮豐濱」「靜浦與長濱之間」→ east_central (Cat 3，花蓮南段)
    if has_hualien and has_fengbin:
        return "east_central"
    if has_fengbin:
        return "east_central"

    # 「成功至臺東間」「臺東成功」→ east_central (Cat 3) — CWA 定義成功在 Cat 3 範圍
    if has_chenggong:
        return "east_central"

    # 「花蓮市」「花蓮縣秀林鄉」(不含成功/宜蘭) → east_central (Cat 3)
    if has_hualien:
        return "east_central"

    # 「秀林」→ east_north (Cat 2，秀林鄉偏北)... 但 CWA 有時歸為 Cat 3
    # 保持 east_central 以匹配更多案例
    if "秀林" in loc:
        return "east_central"

    # 2. 標準單一地名解析
    for kw in EAST_SOUTH_KEYWORDS:
        if kw in loc:
            return "east_south"

    for kw in SOUTH_TIP_KEYWORDS:
        if kw in loc:
            return "south_tip"

    for kw in WEST_SOUTH_KEYWORDS:
        if kw in loc:
            return "west_south"

    for kw in EAST_NORTH_KEYWORDS:
        if kw in loc:
            return "east_north"

    for kw in EAST_CENTRAL_KEYWORDS:
        if kw in loc:
            return "east_central"

    return None


def _compute_context_heading(lats, lons, in_context):
    """計算 context window 整體方向"""
    ctx_lats = lats[in_context]
    ctx_lons = lons[in_context]
    if len(ctx_lats) < 2:
        return 0.0
    dlat = ctx_lats[-1] - ctx_lats[0]
    dlon = (ctx_lons[-1] - ctx_lons[0]) * np.cos(np.radians(ctx_lats[0]))
    return float(np.degrees(np.arctan2(dlat, dlon)))


def _check_crossed_to_west(lats, lons, closest_idx) -> bool:
    """檢查路徑是否穿越到台灣西側"""
    start = max(0, closest_idx - 3)
    post_lats = lats[start:]
    post_lons = lons[start:]
    post_dists = haversine_vec(post_lats, post_lons)
    nearby = post_dists < 400
    if nearby.sum() == 0:
        return False
    return bool(np.any(post_lons[nearby] < WEST_CROSSING_LON))


def _approach_heading(lats: np.ndarray, lons: np.ndarray, closest_idx: int) -> float:
    """計算接近方向（最近點前 3-8 步平均方向）"""
    end = closest_idx
    start = max(0, closest_idx - 8)
    if end - start < 2:
        start = max(0, closest_idx - 3)
        end = min(len(lats) - 1, closest_idx + 3)
    if end <= start:
        return 0.0
    dlats = np.diff(lats[start : end + 1])
    dlons = np.diff(lons[start : end + 1]) * np.cos(
        np.radians(lats[start : end + 1][:-1])
    )
    return float(np.degrees(np.arctan2(np.mean(dlats), np.mean(dlons))))


def classify_typhoon_by_rules(
    track: pd.DataFrame, landfall_location: str = None, buffer_km: float = None
) -> dict:
    """
    根據軌跡幾何特徵分類颱風（路徑類型 1-9）

    buffer_km 設定時：先把路徑裁切到「海岸線外擴 buffer_km」範圍內，
    再以範圍內的幾何進行分類（與其他方法一致，只在範圍內運算）。
    """
    if buffer_km is not None:
        from data_pipiline.stage00_data_ingestion.typhoon.coastline import clip_mask

        keep = clip_mask(
            track["longitude"].values.astype(float),
            track["latitude"].values.astype(float),
            buffer_km,
        )
        track = track[keep]

    lats = track["latitude"].values.astype(float)
    lons = track["longitude"].values.astype(float)

    # === Phase 1: 基礎幾何 ===
    distances = haversine_vec(lats, lons)
    min_dist = float(np.min(distances))
    closest_idx = int(np.argmin(distances))
    closest_lat = float(lats[closest_idx])
    closest_lon = float(lons[closest_idx])

    in_context = distances < CONTEXT_RADIUS_KM
    if in_context.sum() < 2:
        # 未進入 500km，但仍可根據最近點位置判斷海面通過型
        # Cat 9 定義：西北太平洋或南海生成後對台灣無侵襲但有影響者
        # 但若最近點在北方或南方海面，應歸為 Cat 1 或 Cat 5
        if closest_lat > TAIWAN_CENTER_LAT:
            return _result(
                "1",
                0.4,
                f"北部海面遠距通過 (dist={min_dist:.0f}km, lat={closest_lat:.1f})",
                min_dist,
                closest_lat,
                closest_lon,
            )
        else:
            return _result(
                "5",
                0.4,
                f"南部海面遠距通過 (dist={min_dist:.0f}km, lat={closest_lat:.1f})",
                min_dist,
                closest_lat,
                closest_lon,
            )

    ctx_lats = lats[in_context]
    ctx_lons = lons[in_context]
    entry_lat = float(ctx_lats[0])
    entry_lon = float(ctx_lons[0])
    exit_lat = float(ctx_lats[-1])
    exit_lon = float(ctx_lons[-1])

    context_heading = _compute_context_heading(lats, lons, in_context)
    approach = _approach_heading(lats, lons, closest_idx)
    crossed_to_west = _check_crossed_to_west(lats, lons, closest_idx)

    has_landfall_text = landfall_location is not None and str(
        landfall_location
    ).strip() not in ("", "---", "nan", "None", "無登陸")
    landfall_detail = (
        _parse_landfall_detail(landfall_location) if has_landfall_text else None
    )

    confidence = max(0.4, min(0.95, 1.0 - min_dist / 500))
    features = {
        "min_distance_km": round(min_dist, 1),
        "closest_lat": round(closest_lat, 2),
        "closest_lon": round(closest_lon, 2),
        "approach_heading": round(approach, 1),
        "context_heading": round(context_heading, 1),
        "entry_lat": round(entry_lat, 1),
        "exit_lat": round(exit_lat, 1),
        "exit_lon": round(exit_lon, 1),
        "crossed_to_west": crossed_to_west,
        "landfall_detail": landfall_detail,
    }

    # === PRIORITY 1: Cat 6 — 沿東岸/東部海面北上 ===
    # 核心判斷: 不穿越西側 + 東側 + 北行
    # 改進: 若有明確北部登陸文字 → 可能是 Cat 2 而非 Cat 6
    #       收窄 heading 範圍排除 Cat 8 (低角度北行)
    #       收窄 heading 上界排除 Cat 1 (高角度海面通過)
    if (
        not crossed_to_west
        and closest_lon > 120.8
        and 50 < context_heading < 125
        and closest_lat < 26.0
    ):
        east_ratio = np.sum(ctx_lons > TAIWAN_EAST_LON - 0.5) / len(ctx_lons)
        if east_ratio > 0.3:
            # 排除: 非常強的西行接近(>145°) + 很近 → 這是登陸穿越型(Cat 2/3)
            if approach > 145 and min_dist < 60:
                pass  # Skip → fall through to landfall check
            # 排除: 有明確北部登陸文字且登陸距離很近 → Cat 2
            elif (
                has_landfall_text and landfall_detail == "east_north" and min_dist < 80
            ):
                pass  # Skip → Cat 2 landfall check
            # 排除: 有中部登陸文字 + 西行approach(>120°) + 很近 → Cat 3 穿越型
            elif (
                has_landfall_text
                and landfall_detail == "east_central"
                and min_dist < 50
                and approach > 120
            ):
                pass  # Skip → Cat 3 landfall check
            # 排除: 北部海面通過型(最近點偏北+距離遠)
            elif closest_lat > 25.0 and min_dist > 200:
                pass  # likely Cat 1
            else:
                return _result(
                    "6",
                    confidence,
                    f"沿東岸/東部海面北上 (heading={context_heading:.0f}°)",
                    min_dist,
                    closest_lat,
                    closest_lon,
                    features,
                )

    # === PRIORITY 2: Cat 8 — 南部海面向北或東北 ===
    # 改進: 放寬條件以捕獲更多 Cat 8
    if (
        entry_lat < 22.5
        and context_heading < 80
        and not crossed_to_west
        and closest_lon > 120.5
        and closest_lat < 24.0
        and min_dist > 60
    ):
        return _result(
            "8",
            confidence,
            f"南部海面向北或東北 (heading={context_heading:.0f}°)",
            min_dist,
            closest_lat,
            closest_lon,
            features,
        )

    # === PRIORITY 3: 有登陸文字 → 精確分類 ===
    if has_landfall_text and landfall_detail:
        if landfall_detail == "east_north":
            return _result(
                "2",
                confidence,
                f"登陸北部 ({landfall_location})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

        if landfall_detail == "east_central":
            return _result(
                "3",
                confidence,
                f"登陸中部 ({landfall_location})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

        if landfall_detail == "east_south":
            return _result(
                "4",
                confidence,
                f"登陸南部東岸 ({landfall_location})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

        if landfall_detail == "west_south":
            # 西岸: 南方入+北上 → Cat 7; 否則 Cat 9
            if entry_lat < 22.5 and context_heading > 60 and exit_lat > entry_lat + 2.5:
                return _result(
                    "7",
                    confidence,
                    f"從南方經西岸北上 ({landfall_location})",
                    min_dist,
                    closest_lat,
                    closest_lon,
                    features,
                )
            return _result(
                "9",
                confidence * 0.7,
                f"西岸登陸不規則 ({landfall_location})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

        if landfall_detail == "south_tip":
            # 南端: 南方入+北上 → Cat 7; 西行 → Cat 4
            if entry_lat < 22.5 and context_heading > 60 and exit_lat > entry_lat + 3.0:
                return _result(
                    "7",
                    confidence,
                    f"從南方經南端北上 ({landfall_location})",
                    min_dist,
                    closest_lat,
                    closest_lon,
                    features,
                )
            return _result(
                "4",
                confidence * 0.8,
                f"通過南端 ({landfall_location})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

    # === PRIORITY 4: Cat 7 — 南方入、西側北上 ===
    if (
        entry_lat < 22.0
        and closest_lon < 121.0
        and 70 < context_heading < 140
        and exit_lat > entry_lat + 3.0
    ):
        return _result(
            "7",
            confidence,
            f"從南方沿西側北上 (lon={closest_lon:.1f})",
            min_dist,
            closest_lat,
            closest_lon,
            features,
        )

    # === 有登陸但無法解析 → 緯度判斷 ===
    if has_landfall_text:
        if closest_lat >= 24.0:
            return _result(
                "2",
                confidence * 0.8,
                f"登陸按緯度判北部 (lat={closest_lat:.1f})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )
        elif closest_lat <= 22.8:
            return _result(
                "4",
                confidence * 0.8,
                f"登陸按緯度判南部 (lat={closest_lat:.1f})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )
        else:
            return _result(
                "3",
                confidence * 0.8,
                f"登陸按緯度判中部 (lat={closest_lat:.1f})",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

    # === PRIORITY 5: Cat 1/5 — 海面通過 ===
    if min_dist > LANDFALL_KM:
        if closest_lat > TAIWAN_CENTER_LAT:
            return _result(
                "1",
                confidence,
                f"北部海面通過 (lat={closest_lat:.1f}, dist={min_dist:.0f}km)",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )
        else:
            # 南部海面：Cat 7/8 已在 Priority 2/4 檢查過
            # 剩餘的南部海面颱風應為 Cat 5（向西通過）
            return _result(
                "5",
                confidence,
                f"南部海面通過 (lat={closest_lat:.1f}, dist={min_dist:.0f}km)",
                min_dist,
                closest_lat,
                closest_lon,
                features,
            )

    # === PRIORITY 5.5: 接近但無登陸文字 → 緯度判斷 ===
    # 颱風非常接近台灣但沒有登陸文字記錄，用最近點緯度判斷
    if closest_lat >= 24.0:
        return _result(
            "2",
            confidence * 0.7,
            f"無登陸文字，按緯度判北部 (lat={closest_lat:.1f}, dist={min_dist:.0f}km)",
            min_dist,
            closest_lat,
            closest_lon,
            features,
        )
    elif closest_lat <= 22.5:
        return _result(
            "4",
            confidence * 0.7,
            f"無登陸文字，按緯度判南部 (lat={closest_lat:.1f}, dist={min_dist:.0f}km)",
            min_dist,
            closest_lat,
            closest_lon,
            features,
        )
    else:
        return _result(
            "3",
            confidence * 0.7,
            f"無登陸文字，按緯度判中部 (lat={closest_lat:.1f}, dist={min_dist:.0f}km)",
            min_dist,
            closest_lat,
            closest_lon,
            features,
        )


def _result(
    cat, confidence, reasoning, min_dist, closest_lat, closest_lon, features=None
):
    if features is None:
        features = {
            "min_distance_km": round(min_dist, 1),
            "closest_lat": round(closest_lat, 2),
            "closest_lon": round(closest_lon, 2),
        }
    return {
        "predicted_category": cat,
        "confidence": confidence,
        "reasoning": reasoning,
        "features": features,
    }


# =============================================================================
# RuleBasedSimilarity
# =============================================================================


class RuleBasedSimilarity(SimilarityBase):
    """規則分類 + 加權相似度排序"""

    def __init__(
        self, weight_path=0.4, weight_category=0.5, weight_intensity=0.1, buffer_km=None
    ):
        self.weight_path = weight_path
        self.weight_category = weight_category
        self.weight_intensity = weight_intensity
        self.buffer_km = buffer_km
        self._ids: list[str] = []
        self._features_dict = {}
        self._categories: dict[str, str] = {}

    def fit(self, feature_dict: dict, loader=None, **kwargs):
        self._features_dict = feature_dict
        self._ids = list(feature_dict.keys())
        if loader is not None:
            for rec in loader.records:
                if rec.typhoon_id in feature_dict:
                    result = classify_typhoon_by_rules(
                        rec.track, rec.landfall_location, buffer_km=self.buffer_km
                    )
                    self._categories[rec.typhoon_id] = result["predicted_category"]
        print(f"  ✓ 規則式分類器已擬合 {len(self._ids)} 筆颱風")

    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        query_cat = self._categories.get(query_id, "9")
        query_vec = self._features_dict[query_id].to_feature_vector()
        candidates = (
            [tid for tid in self._ids if tid != query_id] if exclude_self else self._ids
        )

        raw_dists = []
        for tid in candidates:
            other_vec = self._features_dict[tid].to_feature_vector()
            raw_dists.append(float(np.linalg.norm(query_vec - other_vec)))
        max_raw = max(raw_dists) if raw_dists else 1.0

        scored = []
        for i, tid in enumerate(candidates):
            other_vec = self._features_dict[tid].to_feature_vector()
            norm_path = raw_dists[i] / (max_raw + 1e-8)
            cat_penalty = 0.0 if self._categories.get(tid) == query_cat else 1.0
            intensity_diff = abs(query_vec[2] - other_vec[2]) / 100.0
            dist = (
                self.weight_path * norm_path
                + self.weight_category * cat_penalty
                + self.weight_intensity * min(intensity_diff, 1.0)
            )
            scored.append((tid, dist))

        scored.sort(key=lambda x: x[1])
        top_k = scored[:k]
        result_ids = [x[0] for x in top_k]
        result_dists = [x[1] for x in top_k]
        max_d = max(result_dists) if result_dists else 1.0
        scores = [1.0 - d / (max_d + 1e-8) for d in result_dists]
        return SimilarityResult(
            query_id=query_id,
            similar_ids=result_ids,
            distances=result_dists,
            scores=scores,
        )

    def compute_distance(self, id_a: str, id_b: str) -> float:
        vec_a = self._features_dict[id_a].to_feature_vector()
        vec_b = self._features_dict[id_b].to_feature_vector()
        raw = float(np.linalg.norm(vec_a - vec_b))
        cat_penalty = (
            0.0 if self._categories.get(id_a) == self._categories.get(id_b) else 1.0
        )
        intensity_diff = abs(vec_a[2] - vec_b[2]) / 100.0
        return (
            self.weight_path * raw
            + self.weight_category * cat_penalty
            + self.weight_intensity * intensity_diff
        )

    def get_rule_category(self, typhoon_id: str) -> str:
        return self._categories.get(typhoon_id, "9")

    def classify_track(
        self, track: pd.DataFrame, landfall_location: str = None
    ) -> dict:
        return classify_typhoon_by_rules(
            track, landfall_location, buffer_km=self.buffer_km
        )

    def get_config(self) -> dict:
        return {
            "method": "rule_based",
            "weight_path": self.weight_path,
            "weight_category": self.weight_category,
            "weight_intensity": self.weight_intensity,
        }
