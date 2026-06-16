"""
特徵工程模組 v2 — 依據 strategy advice 實作修正

核心修正：
1. 座標修正：dx *= cos(lat * π/180) 修正經緯度不等距
2. Impact window 縮小至 300km（核心影響區）
3. 距離加權：exp(-r/200) 讓接近台灣的點權重更高
4. 速度特徵：改為「接近台灣時的速度」(r < 300)
5. Rain proxy：加入迎風面修正 cos(θ - θ_normal)
6. 方位角使用修正後的座標
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

# 台灣中心座標
TAIWAN_LAT = 23.7
TAIWAN_LON = 121.0

# 地球半徑 (km)
EARTH_RADIUS_KM = 6371.0

# Impact window 閾值 (km) — 保持 500km 提取，300km 加權核心
IMPACT_WINDOW_RADIUS_KM = 500.0

# 距離加權核心半徑
CORE_WEIGHT_RADIUS_KM = 300.0

# 台灣迎風面法向量方位角（東北風方向，約 60°）
TAIWAN_NORMAL_THETA_RAD = np.radians(60.0)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine 球面距離 (km)"""
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * np.arcsin(np.sqrt(a))


def haversine_vec(
    lats: np.ndarray,
    lons: np.ndarray,
    ref_lat: float = TAIWAN_LAT,
    ref_lon: float = TAIWAN_LON,
) -> np.ndarray:
    """向量化 Haversine"""
    lat1 = np.radians(lats)
    lon1 = np.radians(lons)
    lat2 = np.radians(ref_lat)
    lon2 = np.radians(ref_lon)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * np.arcsin(np.sqrt(a))


def relative_coordinates(
    lats: np.ndarray, lons: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """
    計算相對台灣的修正座標（修正經緯度不等距問題）

    修正：dx *= cos(lat * π/180)

    Returns:
        (dx, dy) in degree-equivalent units (corrected)
    """
    dy = lats - TAIWAN_LAT
    dx = (lons - TAIWAN_LON) * np.cos(np.radians(lats))
    return dx, dy


def polar_coordinates(
    lats: np.ndarray, lons: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """
    計算修正後的極座標 (r, theta)

    r: haversine 距離 (km)
    theta: 修正後的方位角 (radians)
    """
    r = haversine_vec(lats, lons)
    dx, dy = relative_coordinates(lats, lons)
    theta = np.arctan2(dy, dx)
    return r, theta


@dataclass
class TyphoonFeatures:
    """颱風特徵向量（用於相似度比較）"""

    typhoon_id: str

    # === 靜態 / 摘要特徵 ===
    min_distance_to_taiwan: float  # 最接近台灣的距離 (km)
    mean_angle: float  # 通過台灣時的平均方位角 (rad)
    max_wind_kt: float  # 生命週期最大風速 (kt)
    max_wind_in_window_kt: float  # impact window 內最大風速 (kt)
    approach_speed_kmh: float  # 接近台灣（r<300km）的平均移動速度 (km/h)
    min_pressure_mb: float  # 最低氣壓 (mb)
    intensification_rate: float  # 增強率 (kt / 6h) — impact window 前段
    rain_proxy: float  # 降雨代理指標（含迎風面修正）
    is_landfall: bool  # 是否登陸
    birth_lon: float  # 生成經度
    birth_lat: float  # 生成緯度

    # === Impact Window 路徑（用於 DTW）===
    impact_window_r: np.ndarray = field(repr=False)  # 距台灣距離序列 (km)
    impact_window_theta: np.ndarray = field(repr=False)  # 修正方位角序列 (rad)
    impact_window_wind: np.ndarray = field(repr=False)  # 風速序列 (kt)
    impact_window_pressure: np.ndarray = field(repr=False)  # 氣壓序列 (mb)

    def to_feature_vector(self) -> np.ndarray:
        """轉換為用於 KNN 比較的摘要特徵向量（11 維）"""
        return np.array(
            [
                self.min_distance_to_taiwan,
                self.mean_angle,
                self.max_wind_kt,
                self.max_wind_in_window_kt,
                self.approach_speed_kmh,
                self.min_pressure_mb,
                self.intensification_rate,
                self.rain_proxy,
                float(self.is_landfall),
                self.birth_lon,
                self.birth_lat,
            ],
            dtype=np.float64,
        )

    @staticmethod
    def feature_names() -> list[str]:
        return [
            "min_distance_to_taiwan",
            "mean_angle",
            "max_wind_kt",
            "max_wind_in_window_kt",
            "approach_speed_kmh",
            "min_pressure_mb",
            "intensification_rate",
            "rain_proxy",
            "is_landfall",
            "birth_lon",
            "birth_lat",
        ]

    def get_impact_window_matrix(self) -> np.ndarray:
        """取得 impact window 的多維時序矩陣 (T, 4): [r, theta, wind, pressure]"""
        return np.column_stack(
            [
                self.impact_window_r,
                self.impact_window_theta,
                self.impact_window_wind,
                self.impact_window_pressure,
            ]
        )


class TyphoonFeatureExtractor:
    """
    特徵提取器 v2

    改進：
    1. 修正座標 (cos(lat) 修正)
    2. Impact window = 300km
    3. 距離加權
    4. 接近台灣時速度
    5. 迎風面 rain proxy
    """

    def __init__(self, impact_radius_km: float = IMPACT_WINDOW_RADIUS_KM):
        self.impact_radius_km = impact_radius_km

    def extract(
        self,
        typhoon_id: str,
        track: pd.DataFrame,
        birth_lon: float = None,
        birth_lat: float = None,
        landfall_location: str = None,
    ) -> TyphoonFeatures:
        """
        從單一颱風軌跡提取完整特徵

        Args:
            typhoon_id: 颱風編號
            track: DataFrame with columns [latitude, longitude, wind_kt, pressure_mb, timestamp_utc]
            birth_lon, birth_lat: 生成位置
            landfall_location: 登陸地段

        Returns:
            TyphoonFeatures
        """
        track = track.copy()

        # --- Step 1: 空間標準化（修正座標 → 極座標）---
        lats = track["latitude"].values.astype(float)
        lons = track["longitude"].values.astype(float)

        # 修正極座標
        r, theta = polar_coordinates(lats, lons)

        track["distance_km"] = r
        track["azimuth_rad"] = theta

        # 風速 / 氣壓（處理 None）
        winds = track["wind_kt"].fillna(0).values.astype(float)
        pressures = track["pressure_mb"].fillna(1013).values.astype(float)

        # --- Step 2: 提取 Impact Window (r < 300km) ---
        in_window = r < self.impact_radius_km
        if in_window.sum() < 2:
            # 如果路徑沒有進入 300km 範圍，取最近的 5 個點
            nearest_indices = np.argsort(r)[: max(5, len(r) // 5)]
            in_window = np.zeros(len(r), dtype=bool)
            in_window[nearest_indices] = True

        window_r = r[in_window]
        window_theta = theta[in_window]
        window_wind = winds[in_window]
        window_pressure = pressures[in_window]

        # --- Step 3: 摘要特徵 ---

        # 最接近台灣的距離
        min_distance = float(np.min(r))

        # 通過台灣時的平均方位角（使用 circular mean）
        mean_angle = float(
            np.arctan2(np.mean(np.sin(window_theta)), np.mean(np.cos(window_theta)))
        )

        # 最大風速（全生命週期 & impact window）
        max_wind = float(np.max(winds)) if len(winds) > 0 else 0.0
        max_wind_window = float(np.max(window_wind)) if len(window_wind) > 0 else 0.0

        # 移動速度（接近台灣 r < 300km 階段）
        approach_speed = self._compute_approach_speed(track, in_window)

        # 最低氣壓
        valid_pressures = pressures[pressures < 1013]
        min_pressure = (
            float(np.min(valid_pressures)) if len(valid_pressures) > 0 else 1013.0
        )

        # 增強率（impact window 前段）
        intensification = self._compute_intensification(winds, in_window)

        # Rain proxy v2: 含迎風面修正
        # rain_proxy = wind * max(0, cos(θ - θ_normal)) / distance
        safe_r = np.maximum(window_r, 1.0)
        wind_direction_factor = np.maximum(
            0, np.cos(window_theta - TAIWAN_NORMAL_THETA_RAD)
        )
        rain_proxy = float(
            np.mean(window_wind * (0.5 + 0.5 * wind_direction_factor) / safe_r)
        )

        # 是否登陸
        is_landfall = landfall_location is not None and str(
            landfall_location
        ).strip() not in ("", "---", "nan", "None")

        # 生成位置
        _birth_lon = birth_lon if birth_lon is not None else float(lons[0])
        _birth_lat = birth_lat if birth_lat is not None else float(lats[0])

        return TyphoonFeatures(
            typhoon_id=typhoon_id,
            min_distance_to_taiwan=min_distance,
            mean_angle=mean_angle,
            max_wind_kt=max_wind,
            max_wind_in_window_kt=max_wind_window,
            approach_speed_kmh=approach_speed,
            min_pressure_mb=min_pressure,
            intensification_rate=intensification,
            rain_proxy=rain_proxy,
            is_landfall=is_landfall,
            birth_lon=_birth_lon,
            birth_lat=_birth_lat,
            impact_window_r=window_r,
            impact_window_theta=window_theta,
            impact_window_wind=window_wind,
            impact_window_pressure=window_pressure,
        )

    def extract_all(self, loader) -> dict[str, TyphoonFeatures]:
        """
        從 DataLoader 批量提取所有颱風的特徵

        Args:
            loader: DataLoader instance (已 load)

        Returns:
            {typhoon_id: TyphoonFeatures}
        """
        features = {}
        for rec in loader.records:
            feat = self.extract(
                typhoon_id=rec.typhoon_id,
                track=rec.track,
                birth_lon=rec.birth_lon,
                birth_lat=rec.birth_lat,
                landfall_location=rec.landfall_location,
            )
            features[rec.typhoon_id] = feat
        print(
            f"✓ 已提取 {len(features)} 筆颱風特徵（impact window={self.impact_radius_km}km）"
        )
        return features

    # ---- 內部計算方法 ----

    def _compute_approach_speed(
        self, track: pd.DataFrame, in_window: np.ndarray
    ) -> float:
        """計算在 impact window（r < 300km）內的平均移動速度 (km/h)"""
        if in_window.sum() < 2:
            return 0.0

        window_track = track[in_window].reset_index(drop=True)
        lats = window_track["latitude"].values
        lons = window_track["longitude"].values

        total_dist = 0.0
        for i in range(1, len(lats)):
            total_dist += haversine(lats[i - 1], lons[i - 1], lats[i], lons[i])

        # 時間計算
        if "timestamp_utc" in window_track.columns and pd.notna(
            window_track["timestamp_utc"].iloc[0]
        ):
            times = pd.to_datetime(window_track["timestamp_utc"])
            dt_hours = (times.iloc[-1] - times.iloc[0]).total_seconds() / 3600
            if dt_hours > 0:
                return total_dist / dt_hours

        # fallback: 假設 3 小時間隔
        n_steps = len(lats) - 1
        hours = n_steps * 3
        return total_dist / max(hours, 1)

    def _compute_intensification(
        self, winds: np.ndarray, in_window: np.ndarray
    ) -> float:
        """計算 impact window 前半段的風速增強率 (kt / step)"""
        window_indices = np.where(in_window)[0]
        if len(window_indices) < 2:
            return 0.0

        # 取 impact window 前半段
        half = max(2, len(window_indices) // 2)
        first_half = winds[window_indices[:half]]

        if len(first_half) < 2:
            return 0.0

        # 線性回歸的斜率
        x = np.arange(len(first_half))
        slope = np.polyfit(x, first_half, 1)[0]
        return float(slope)
