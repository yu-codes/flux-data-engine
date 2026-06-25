"""地圖投影視覺化：台灣島、海岸線外擴緩衝範圍、查詢路徑與 Top-K 相似路徑。"""

from __future__ import annotations

import matplotlib.pyplot as plt

from .geometry import TAIWAN_OUTLINE_LONGITUDE_LATITUDE, buffer_polygon


def _apply_chinese_font():
    plt.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei",
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK TC",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def plot_prediction(reference, query_track, prediction_result, buffer_kilometers, axes=None, show=True):
    """繪製查詢路徑與 Top-K 相似颱風路徑，疊上海岸線外擴 buffer_kilometers 緩衝範圍。

    reference：prepare_reference() 產生的參考特徵快取（提供 record_by_id 以取得颱風資訊）。
    """
    _apply_chinese_font()

    outline = TAIWAN_OUTLINE_LONGITUDE_LATITUDE + [TAIWAN_OUTLINE_LONGITUDE_LATITUDE[0]]
    buffer_points = buffer_polygon(buffer_kilometers)
    buffer_points = list(buffer_points) + [buffer_points[0]]

    if axes is None:
        _, axes = plt.subplots(figsize=(9, 9))
    axes.fill(
        [point[0] for point in buffer_points],
        [point[1] for point in buffer_points],
        color="#387eb8",
        alpha=0.08,
    )
    axes.plot(
        [point[0] for point in buffer_points],
        [point[1] for point in buffer_points],
        "--",
        color="#387eb8",
        lw=1,
        label=f"海岸線外擴 {buffer_kilometers}km",
    )
    axes.fill(
        [point[0] for point in outline],
        [point[1] for point in outline],
        color="#d9e8c8",
        ec="#6b8e4e",
    )
    for typhoon_id in prediction_result["ids"]:
        record = reference.record_by_id[typhoon_id]
        axes.plot(
            record["track"]["longitude"],
            record["track"]["latitude"],
            lw=1.6,
            alpha=0.75,
            label=f"{record['name_zh']} ({record['year']})",
        )
    axes.plot(query_track["longitude"], query_track["latitude"], "r-", lw=3, label="查詢路徑")
    axes.plot(
        query_track["longitude"].iloc[0],
        query_track["latitude"].iloc[0],
        "ro",
    )
    axes.set_xlabel("經度 (°E)")
    axes.set_ylabel("緯度 (°N)")
    axes.set_title(f"Coastline RRF 預測（計算範圍：海岸線外擴 {buffer_kilometers} km）")
    axes.legend(fontsize=8, loc="upper right")
    axes.grid(alpha=0.3)
    if show:
        plt.show()
    return axes
