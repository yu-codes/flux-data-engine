"""相似度演算法（純函數）：KNN / Coastline(Chamfer) / RRF 融合 + 類比投票。

- KNN      ：標準化加權特徵的歐式距離排名
- Coastline：範圍內路徑的對稱平均最近點（Chamfer）距離排名 —— 絕對位置
- RRF      ：以名次融合三訊號（Coastline 主導 + KNN + 可選降水）

注意：本方法為「類比 / 非參數」預測，**沒有需要訓練的模型**。
prepare_reference() 只是把參考颱風的特徵與軌跡先算好（純資料快取），
讓 predict() 等算法函數可重複取用、避免每次預測重算；融合權重等參數皆由 Configuration 提供。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Configuration
from .features import extract_features
from .geometry import clip_mask, distances_to_coast_kilometers, geographic_to_kilometers


# ---- Chamfer（公里平面） ----
def _point_to_polyline_distance(point, polyline):
    if len(polyline) == 1:
        return float(np.hypot(*(point - polyline[0])))
    segment_start = polyline[:-1]
    segment_end = polyline[1:]
    segment_vector = segment_end - segment_start
    start_to_point = point - segment_start
    segment_length_squared = np.einsum("ij,ij->i", segment_vector, segment_vector)
    projection_fraction = np.where(
        segment_length_squared > 1e-12,
        np.einsum("ij,ij->i", start_to_point, segment_vector)
        / np.maximum(segment_length_squared, 1e-12),
        0.0,
    )
    projection_fraction = np.clip(projection_fraction, 0, 1)
    projection = segment_start + projection_fraction[:, None] * segment_vector
    return float(
        np.min(np.hypot(projection[:, 0] - point[0], projection[:, 1] - point[1]))
    )


def chamfer_kilometers(query_points, candidate_points):
    if len(query_points) == 0 or len(candidate_points) == 0:
        return float("inf")
    query_to_candidate = np.mean(
        [_point_to_polyline_distance(point, candidate_points) for point in query_points]
    )
    candidate_to_query = np.mean(
        [_point_to_polyline_distance(point, query_points) for point in candidate_points]
    )
    return 0.5 * (query_to_candidate + candidate_to_query)


def path_offset_kilometers(
    longitudes_a, latitudes_a, longitudes_b, latitudes_b, buffer_kilometers
):
    """兩條路徑在範圍內的平均偏離 (公里) — 顯示用絕對指標（必 > 0）。"""
    mask_a = clip_mask(longitudes_a, latitudes_a, buffer_kilometers)
    mask_b = clip_mask(longitudes_b, latitudes_b, buffer_kilometers)
    x_a, y_a = geographic_to_kilometers(longitudes_a, latitudes_a)
    x_b, y_b = geographic_to_kilometers(longitudes_b, latitudes_b)
    return chamfer_kilometers(
        np.column_stack([x_a, y_a])[mask_a], np.column_stack([x_b, y_b])[mask_b]
    )


# =============================================================================
# 參考特徵快取（純資料，非「模型」）
# =============================================================================
@dataclass
class ReferenceIndex:
    """預先計算好的參考颱風特徵／軌跡快取，供算法函數重複取用。

    這不是訓練出來的模型，只是把每筆參考颱風的：
      - 11 維特徵（標準化加權後的 standardized_features，供 KNN）
      - 公里平面軌跡與到海岸距離（供 Coastline 依範圍裁切）
    先算好存起來，避免每次預測重算。
    """

    typhoon_ids: list
    record_by_id: dict
    categories_by_id: dict
    feature_weights: np.ndarray
    feature_mean: np.ndarray
    feature_std: np.ndarray
    standardized_features: np.ndarray
    tracks_kilometers: dict
    distances_to_coast: dict
    buffer_kilometers: float


def prepare_reference(records, configuration: Configuration, buffer_kilometers=None) -> ReferenceIndex:
    """以指定計算範圍（海岸線外擴公里）預先計算參考颱風的特徵與軌跡快取。"""
    if buffer_kilometers is None:
        buffer_kilometers = configuration.buffer_kilometers
    feature_weights = configuration.feature_weights_array
    # 特徵 + 標準化（等同 StandardScaler）
    feature_matrix = np.array(
        [
            extract_features(record["track"], buffer_kilometers, record["landfall"])
            for record in records
        ]
    )
    feature_mean = feature_matrix.mean(axis=0)
    feature_std = feature_matrix.std(axis=0)
    feature_std[feature_std == 0] = 1.0
    standardized_features = ((feature_matrix - feature_mean) / feature_std) * feature_weights
    # 每筆公里軌跡 + 到海岸距離（供 coastline 依範圍裁切）
    tracks_kilometers, distances_to_coast = {}, {}
    for record in records:
        longitudes = record["track"]["longitude"].values.astype(float)
        latitudes = record["track"]["latitude"].values.astype(float)
        x_kilometers, y_kilometers = geographic_to_kilometers(longitudes, latitudes)
        tracks_kilometers[record["id"]] = np.column_stack([x_kilometers, y_kilometers])
        distances_to_coast[record["id"]] = distances_to_coast_kilometers(longitudes, latitudes)
    return ReferenceIndex(
        typhoon_ids=[record["id"] for record in records],
        record_by_id={record["id"]: record for record in records},
        categories_by_id={record["id"]: record["category"] for record in records},
        feature_weights=feature_weights,
        feature_mean=feature_mean,
        feature_std=feature_std,
        standardized_features=standardized_features,
        tracks_kilometers=tracks_kilometers,
        distances_to_coast=distances_to_coast,
        buffer_kilometers=buffer_kilometers,
    )


# =============================================================================
# 三組排名 + RRF 融合 + 投票（純函數）
# =============================================================================
def rank_knn(reference: ReferenceIndex, query_features, excluded_id=None):
    """KNN 排名：標準化加權特徵歐式距離由近到遠。"""
    distances = np.linalg.norm(reference.standardized_features - query_features, axis=1)
    order = np.argsort(distances)
    return [
        reference.typhoon_ids[index]
        for index in order
        if reference.typhoon_ids[index] != excluded_id
    ]


def rank_coastline(reference: ReferenceIndex, query_track_kilometers, buffer_kilometers, excluded_id=None):
    """Coastline 排名：範圍內路徑 Chamfer 距離由近到遠（絕對位置相似度）。"""
    scored = []
    for typhoon_id in reference.typhoon_ids:
        if typhoon_id == excluded_id:
            continue
        candidate_track = reference.tracks_kilometers[typhoon_id][
            reference.distances_to_coast[typhoon_id] <= buffer_kilometers
        ]
        if len(candidate_track) == 0:
            continue
        distance = chamfer_kilometers(query_track_kilometers, candidate_track)
        if np.isfinite(distance):
            scored.append((typhoon_id, distance))
    scored.sort(key=lambda item: item[1])
    return [typhoon_id for typhoon_id, _ in scored]


def rank_rainfall(reference: ReferenceIndex, query_rainfall, region, excluded_id=None):
    """降水排名：與查詢事件降水量差距由小到大；缺值排在最後。"""
    if query_rainfall is None:
        return {}
    scored, missing = [], []
    for typhoon_id in reference.typhoon_ids:
        if typhoon_id == excluded_id:
            continue
        rainfall_value = reference.record_by_id[typhoon_id]["rainfall"].get(region)
        if rainfall_value is None:
            missing.append(typhoon_id)
        else:
            scored.append((typhoon_id, abs(query_rainfall - rainfall_value)))
    scored.sort(key=lambda item: item[1])
    ranks = {typhoon_id: rank for rank, (typhoon_id, _) in enumerate(scored)}
    for rank, typhoon_id in enumerate(missing, len(scored)):
        ranks[typhoon_id] = rank
    return ranks


def fuse_rankings(
    reference: ReferenceIndex,
    coastline_ids,
    knn_ids,
    rainfall_ranks,
    configuration: Configuration,
    neighbor_count,
    use_rainfall,
):
    """RRF 名次融合，回傳 (typhoon_ids, 投票用正規化距離)。"""
    pool_size = configuration.candidate_pool_size
    coastline_ids = coastline_ids[:pool_size]
    knn_ids = knn_ids[:pool_size]  # 只取候選池內名次（其餘視為 pool_size）
    coastline_ranks = {typhoon_id: rank for rank, typhoon_id in enumerate(coastline_ids)}
    knn_ranks = {typhoon_id: rank for rank, typhoon_id in enumerate(knn_ids)}
    candidates = set(coastline_ids) | set(knn_ids)
    effective_rainfall_weight = (
        configuration.rainfall_weight if (use_rainfall and rainfall_ranks) else 0.0
    )
    if effective_rainfall_weight > 0:
        candidates |= set(
            sorted(rainfall_ranks, key=rainfall_ranks.get)[:pool_size]
        )
    fused_scores = {}
    for typhoon_id in candidates:
        score = configuration.coastline_weight / (
            configuration.rrf_constant + coastline_ranks.get(typhoon_id, pool_size)
        ) + configuration.knn_weight / (
            configuration.rrf_constant + knn_ranks.get(typhoon_id, pool_size)
        )
        if effective_rainfall_weight > 0:
            score += effective_rainfall_weight / (
                configuration.rrf_constant
                + rainfall_ranks.get(typhoon_id, len(reference.typhoon_ids))
            )
        fused_scores[typhoon_id] = score
    top = sorted(fused_scores.items(), key=lambda item: item[1], reverse=True)[:neighbor_count]
    typhoon_ids = [typhoon_id for typhoon_id, _ in top]
    raw_scores = [score for _, score in top]
    maximum_score = max(raw_scores) if raw_scores else 1.0
    normalized_scores = [score / (maximum_score + 1e-8) for score in raw_scores]
    return typhoon_ids, [1.0 - score for score in normalized_scores]


def vote(reference: ReferenceIndex, typhoon_ids, distances):
    """類比投票：相似颱風依 exp(-距離) 對其侵臺分類加權投票。"""
    vote_weights = {}
    for typhoon_id, distance in zip(typhoon_ids, distances):
        category = reference.categories_by_id.get(typhoon_id)
        if category is None:
            continue
        vote_weights[category] = vote_weights.get(category, 0.0) + float(np.exp(-distance))
    if not vote_weights:
        return None, 0.0, {}
    total_weight = sum(vote_weights.values())
    probabilities = {category: weight / total_weight for category, weight in vote_weights.items()}
    predicted_category = max(probabilities, key=probabilities.get)
    return predicted_category, probabilities[predicted_category], probabilities


def predict(
    reference: ReferenceIndex,
    track_dataframe,
    configuration: Configuration,
    neighbor_count=None,
    query_rainfall=None,
    excluded_id=None,
    use_rainfall=None,
):
    """以查詢颱風路徑（DataFrame）跑完整 coastline_rrf 演算法並回傳預測。

    回傳 dict：predicted / confidence / votes / ids
    """
    if neighbor_count is None:
        neighbor_count = configuration.neighbor_count
    if use_rainfall is None:
        use_rainfall = configuration.use_rainfall
    longitudes = track_dataframe["longitude"].values.astype(float)
    latitudes = track_dataframe["latitude"].values.astype(float)
    query_track_kilometers = np.column_stack(
        geographic_to_kilometers(longitudes, latitudes)
    )[clip_mask(longitudes, latitudes, reference.buffer_kilometers)]
    query_features = (
        (
            extract_features(
                track_dataframe,
                reference.buffer_kilometers,
                track_dataframe["landfall"].iloc[0] if "landfall" in track_dataframe else None,
            )
            - reference.feature_mean
        )
        / reference.feature_std
    ) * reference.feature_weights
    coastline_ids = rank_coastline(
        reference, query_track_kilometers, reference.buffer_kilometers, excluded_id
    )
    knn_ids = rank_knn(reference, query_features, excluded_id)
    rainfall_ranks = (
        rank_rainfall(reference, query_rainfall, configuration.rainfall_region, excluded_id)
        if use_rainfall
        else {}
    )
    typhoon_ids, distances = fuse_rankings(
        reference, coastline_ids, knn_ids, rainfall_ranks, configuration, neighbor_count, use_rainfall
    )
    predicted_category, confidence, votes = vote(reference, typhoon_ids, distances)
    return {
        "predicted": predicted_category,
        "confidence": confidence,
        "votes": votes,
        "ids": typhoon_ids,
    }
