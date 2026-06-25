"""特徵擷取：先把路徑裁切到海岸線外擴 buffer_kilometers 範圍內，再計算 11 維摘要特徵（供 KNN）。

地球半徑、台灣參考點等屬演算法常數，定義於本模組，不放在 Configuration。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .geometry import clip_mask

TAIWAN_LATITUDE, TAIWAN_LONGITUDE, EARTH_RADIUS_KILOMETERS = 23.7, 121.0, 6371.0
NORMAL_DIRECTION_RADIANS = np.radians(60.0)


def haversine_distance(latitude_1, longitude_1, latitude_2, longitude_2):
    """兩經緯度點之間的大圓距離（公里）。"""
    latitude_1, longitude_1, latitude_2, longitude_2 = map(
        np.radians, [latitude_1, longitude_1, latitude_2, longitude_2]
    )
    haversine_term = (
        np.sin((latitude_2 - latitude_1) / 2) ** 2
        + np.cos(latitude_1)
        * np.cos(latitude_2)
        * np.sin((longitude_2 - longitude_1) / 2) ** 2
    )
    return EARTH_RADIUS_KILOMETERS * 2 * np.arcsin(np.sqrt(haversine_term))


def haversine_to_reference(
    latitudes, longitudes, reference_latitude=TAIWAN_LATITUDE, reference_longitude=TAIWAN_LONGITUDE
):
    """一組經緯度點到參考點（預設台灣中心）的大圓距離（公里）。"""
    latitudes = np.radians(latitudes)
    longitudes = np.radians(longitudes)
    reference_latitude = np.radians(reference_latitude)
    reference_longitude = np.radians(reference_longitude)
    haversine_term = (
        np.sin((reference_latitude - latitudes) / 2) ** 2
        + np.cos(latitudes)
        * np.cos(reference_latitude)
        * np.sin((reference_longitude - longitudes) / 2) ** 2
    )
    return EARTH_RADIUS_KILOMETERS * 2 * np.arcsin(np.sqrt(haversine_term))


def extract_features(track, buffer_kilometers, landfall=None):
    """回傳 11 維特徵向量（路徑先裁切到海岸線外擴 buffer_kilometers 範圍內）。"""
    track = track.copy()
    keep = clip_mask(
        track["longitude"].values.astype(float),
        track["latitude"].values.astype(float),
        buffer_kilometers,
    )
    track = track[keep].reset_index(drop=True)
    latitudes = track["latitude"].values.astype(float)
    longitudes = track["longitude"].values.astype(float)
    distances = haversine_to_reference(latitudes, longitudes)
    delta_latitude = latitudes - TAIWAN_LATITUDE
    delta_longitude = (longitudes - TAIWAN_LONGITUDE) * np.cos(np.radians(latitudes))
    angles = np.arctan2(delta_latitude, delta_longitude)
    wind_speeds = (
        track["wind_kt"].fillna(0).values.astype(float)
        if "wind_kt" in track
        else np.zeros(len(latitudes))
    )
    pressures = (
        track["pressure_mb"].fillna(1013).values.astype(float)
        if "pressure_mb" in track
        else np.full(len(latitudes), 1013.0)
    )
    minimum_distance = float(np.min(distances))
    mean_angle = float(np.arctan2(np.mean(np.sin(angles)), np.mean(np.cos(angles))))
    maximum_wind = float(np.max(wind_speeds)) if len(wind_speeds) else 0.0
    # 接近速度（裁切段內）
    if len(latitudes) >= 2:
        total_distance = sum(
            haversine_distance(
                latitudes[index - 1], longitudes[index - 1], latitudes[index], longitudes[index]
            )
            for index in range(1, len(latitudes))
        )
        duration_hours = None
        if "timestamp_utc" in track and pd.notna(track["timestamp_utc"].iloc[0]):
            timestamps = pd.to_datetime(track["timestamp_utc"])
            elapsed_hours = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 3600
            duration_hours = elapsed_hours if elapsed_hours > 0 else None
        approach_speed = (
            total_distance / duration_hours
            if duration_hours
            else total_distance / max((len(latitudes) - 1) * 3, 1)
        )
    else:
        approach_speed = 0.0
    valid_pressures = pressures[pressures < 1013]
    minimum_pressure = float(np.min(valid_pressures)) if len(valid_pressures) else 1013.0
    # 增強率（前半段斜率）
    half_length = max(2, len(wind_speeds) // 2)
    first_half_winds = wind_speeds[:half_length]
    intensification_rate = (
        float(np.polyfit(np.arange(len(first_half_winds)), first_half_winds, 1)[0])
        if len(first_half_winds) >= 2
        else 0.0
    )
    safe_distances = np.maximum(distances, 1.0)
    wind_direction_factor = np.maximum(0, np.cos(angles - NORMAL_DIRECTION_RADIANS))
    rainfall_proxy = (
        float(np.mean(wind_speeds * (0.5 + 0.5 * wind_direction_factor) / safe_distances))
        if len(wind_speeds)
        else 0.0
    )
    has_landfall = landfall is not None and str(landfall).strip() not in (
        "",
        "---",
        "nan",
        "None",
    )
    birth_longitude = float(longitudes[0])
    birth_latitude = float(latitudes[0])
    return np.array(
        [
            minimum_distance,
            mean_angle,
            maximum_wind,
            maximum_wind,
            approach_speed,
            minimum_pressure,
            intensification_rate,
            rainfall_proxy,
            float(has_landfall),
            birth_longitude,
            birth_latitude,
        ],
        float,
    )
