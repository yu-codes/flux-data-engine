"""台灣海岸線幾何：經緯度↔本地公里投影、到海岸線距離、範圍裁切、外擴緩衝多邊形。

此處的常數（投影參考點、台灣本島輪廓）屬於演算法本身，非使用者輸入，故不放在 Configuration。
"""

from __future__ import annotations

import numpy as np

# ---- 投影參考點（台灣中心）與每度公里數 ----
REFERENCE_LATITUDE, REFERENCE_LONGITUDE = 23.7, 121.0
KILOMETERS_PER_DEGREE_LATITUDE, KILOMETERS_PER_DEGREE_LONGITUDE = 110.57, 111.32
_COSINE_REFERENCE = np.cos(np.radians(REFERENCE_LATITUDE))

# ---- 台灣本島海岸線輪廓（順時針，(longitude, latitude)）----
TAIWAN_OUTLINE_LONGITUDE_LATITUDE = [
    (121.53, 25.30),
    (121.69, 25.16),
    (122.00, 25.01),
    (121.86, 24.85),
    (121.85, 24.60),
    (121.75, 24.13),
    (121.55, 23.78),
    (121.50, 23.40),
    (121.40, 23.10),
    (121.20, 22.80),
    (121.00, 22.45),
    (120.90, 22.00),
    (120.85, 21.90),
    (120.74, 21.93),
    (120.55, 22.30),
    (120.30, 22.55),
    (120.15, 22.95),
    (120.10, 23.30),
    (120.13, 23.70),
    (120.50, 24.30),
    (120.78, 24.65),
    (121.00, 25.00),
    (121.25, 25.13),
    (121.40, 25.28),
]


def geographic_to_kilometers(longitude, latitude):
    """把經緯度轉成以台灣中心為原點的本地公里平面座標 (x, y)。"""
    longitude = np.asarray(longitude, float)
    latitude = np.asarray(latitude, float)
    x_kilometers = (longitude - REFERENCE_LONGITUDE) * KILOMETERS_PER_DEGREE_LONGITUDE * _COSINE_REFERENCE
    y_kilometers = (latitude - REFERENCE_LATITUDE) * KILOMETERS_PER_DEGREE_LATITUDE
    return x_kilometers, y_kilometers


def kilometers_to_geographic(x_kilometers, y_kilometers):
    """把本地公里平面座標轉回經緯度 (longitude, latitude)。"""
    x_kilometers = np.asarray(x_kilometers, float)
    y_kilometers = np.asarray(y_kilometers, float)
    longitude = REFERENCE_LONGITUDE + x_kilometers / (
        KILOMETERS_PER_DEGREE_LONGITUDE * _COSINE_REFERENCE
    )
    latitude = REFERENCE_LATITUDE + y_kilometers / KILOMETERS_PER_DEGREE_LATITUDE
    return longitude, latitude


def _outline_kilometers():
    points = np.array(TAIWAN_OUTLINE_LONGITUDE_LATITUDE, float)
    x_kilometers, y_kilometers = geographic_to_kilometers(points[:, 0], points[:, 1])
    return np.column_stack([x_kilometers, y_kilometers])


def _points_in_polygon(point_x, point_y, polygon):
    vertex_count = len(polygon)
    inside = np.zeros(len(point_x), bool)
    previous = vertex_count - 1
    for current in range(vertex_count):
        current_x, current_y = polygon[current]
        previous_x, previous_y = polygon[previous]
        crossing = ((current_y > point_y) != (previous_y > point_y)) & (
            point_x
            < (previous_x - current_x) * (point_y - current_y) / (previous_y - current_y + 1e-12)
            + current_x
        )
        inside ^= crossing
        previous = current
    return inside


def _points_to_polyline_minimum_distance(point_x, point_y, polyline):
    segment_start = polyline
    segment_end = np.roll(polyline, -1, axis=0)
    segment_vector = segment_end - segment_start
    segment_length_squared = np.maximum(
        np.einsum("md,md->m", segment_vector, segment_vector), 1e-12
    )
    query_points = np.column_stack([point_x, point_y])
    start_to_point = query_points[:, None, :] - segment_start[None, :, :]
    projection_fraction = np.clip(
        np.einsum("nmd,md->nm", start_to_point, segment_vector) / segment_length_squared,
        0.0,
        1.0,
    )
    projection = segment_start[None, :, :] + projection_fraction[:, :, None] * segment_vector[None, :, :]
    distances = np.hypot(
        projection[:, :, 0] - point_x[:, None], projection[:, :, 1] - point_y[:, None]
    )
    return distances.min(axis=1)


def distances_to_coast_kilometers(longitudes, latitudes):
    """每個經緯度點到台灣海岸線的最短距離（公里）；位於本島內部者為 0。"""
    polygon = _outline_kilometers()
    x_kilometers, y_kilometers = geographic_to_kilometers(longitudes, latitudes)
    x_kilometers = np.atleast_1d(x_kilometers).astype(float)
    y_kilometers = np.atleast_1d(y_kilometers).astype(float)
    distances = _points_to_polyline_minimum_distance(x_kilometers, y_kilometers, polygon)
    distances[_points_in_polygon(x_kilometers, y_kilometers, polygon)] = 0.0
    return distances


def clip_mask(longitudes, latitudes, buffer_kilometers, minimum_points=2):
    """回傳布林遮罩：路徑點到海岸線距離 <= buffer_kilometers。不足者取最近點補足。"""
    longitudes = np.asarray(longitudes, float)
    latitudes = np.asarray(latitudes, float)
    distances = distances_to_coast_kilometers(longitudes, latitudes)
    mask = distances <= buffer_kilometers
    if mask.sum() < minimum_points:
        nearest = np.argsort(distances)[: max(minimum_points, len(distances) // 5)]
        mask = np.zeros(len(distances), bool)
        mask[nearest] = True
    return mask


def _convex_hull(points):
    sorted_points = np.array(sorted(map(tuple, points)))

    def cross_product(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (
            first[1] - origin[1]
        ) * (second[0] - origin[0])

    lower = []
    for point in sorted_points:
        while len(lower) >= 2 and cross_product(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(sorted_points):
        while len(upper) >= 2 and cross_product(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return np.array(lower[:-1] + upper[:-1])


def buffer_polygon(buffer_kilometers, arc_steps=6):
    """台灣海岸線（凸包）向外擴張 buffer_kilometers 的緩衝多邊形，回傳 [(longitude, latitude), ...]。"""
    hull = _convex_hull(_outline_kilometers())
    vertex_count = len(hull)
    normals = np.zeros((vertex_count, 2))
    for index in range(vertex_count):
        vertex = hull[index]
        next_vertex = hull[(index + 1) % vertex_count]
        edge_x, edge_y = next_vertex[0] - vertex[0], next_vertex[1] - vertex[1]
        edge_length = np.hypot(edge_x, edge_y) + 1e-12
        normals[index] = (edge_y / edge_length, -edge_x / edge_length)
    output_points = []
    for index in range(vertex_count):
        vertex = hull[index]
        normal = normals[index]
        previous_normal = normals[(index - 1) % vertex_count]
        angle_start = np.arctan2(previous_normal[1], previous_normal[0])
        angle_end = np.arctan2(normal[1], normal[0])
        if angle_end < angle_start:
            angle_end += 2 * np.pi
        step_count = max(1, int(np.ceil((angle_end - angle_start) / (np.pi / 2 / arc_steps))))
        for step in range(step_count + 1):
            angle = angle_start + (angle_end - angle_start) * step / step_count
            output_points.append(
                (
                    vertex[0] + buffer_kilometers * np.cos(angle),
                    vertex[1] + buffer_kilometers * np.sin(angle),
                )
            )
        next_vertex = hull[(index + 1) % vertex_count]
        output_points.append(
            (next_vertex[0] + buffer_kilometers * normal[0], next_vertex[1] + buffer_kilometers * normal[1])
        )
    output_array = np.array(output_points)
    longitudes, latitudes = kilometers_to_geographic(output_array[:, 0], output_array[:, 1])
    return list(zip(longitudes, latitudes))
