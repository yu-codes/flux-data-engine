"""高階流程：整理查詢路徑、相似颱風表、留一驗證，以及★主預測入口函數。

★★★ 對外主入口請見本檔的 predict_typhoons() 與 predict_typhoons_over_buffers() ★★★
這兩個函數即「輸入參考資料、計算範圍等參數 → 得到數個預測颱風結果」的進入點。

整體無「模型」概念：類比預測為非參數方法，只有算法函數與一份參考特徵快取。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Configuration
from .similarity import ReferenceIndex, predict, prepare_reference, path_offset_kilometers


def to_track_dataframe(query_track):
    """把查詢路徑（list[dict] 或 DataFrame）轉成標準 DataFrame，補上時間戳。"""
    track_dataframe = (
        query_track if isinstance(query_track, pd.DataFrame) else pd.DataFrame(query_track)
    )
    track_dataframe = track_dataframe.copy()
    if "timestamp_utc" not in track_dataframe.columns:
        track_dataframe["timestamp_utc"] = pd.date_range(
            "2024-01-01", periods=len(track_dataframe), freq="6h"
        )
    return track_dataframe


def similar_typhoons_table(
    reference: ReferenceIndex, query_track, prediction_result, buffer_kilometers, configuration: Configuration
):
    """整理 Top-K 相似颱風為表格（含平均偏離公里與相似度 %）。"""
    track_dataframe = to_track_dataframe(query_track)
    rows = []
    for typhoon_id in prediction_result["ids"]:
        record = reference.record_by_id[typhoon_id]
        offset = path_offset_kilometers(
            track_dataframe["longitude"].values,
            track_dataframe["latitude"].values,
            record["track"]["longitude"].values,
            record["track"]["latitude"].values,
            buffer_kilometers,
        )
        rows.append(
            {
                "颱風": f"{record['name_zh']} ({record['name_en']})",
                "年份": record["year"],
                "侵臺分類": record["category"],
                "平均偏離(km)": round(float(offset), 1),
                "相似度(%)": round(
                    float(np.exp(-offset / configuration.score_scale_kilometers)) * 100, 1
                ),
            }
        )
    return pd.DataFrame(rows)


# =============================================================================
# ★★★ 主預測入口函數 ★★★
# =============================================================================
def predict_typhoons(
    configuration: Configuration,
    query_track,
    records,
    buffer_kilometers=None,
    use_rainfall=None,
    query_rainfall=None,
    reference=None,
):
    """【主入口】輸入「參考資料＋計算範圍＋查詢路徑」，回傳數個最相似的預測颱風結果。

    這是整支流程的核心進入點：給定參數即可得到一組預測（Top-K 相似颱風 + 侵臺分類）。

    參數
    ----
    configuration   : Configuration 參數物件（提供 neighbor_count、權重、降水設定等預設值）
    query_track     : 查詢颱風路徑，list[dict] 或 DataFrame（需含 latitude/longitude）
    records         : 由 data_loader.load_records 載入的參考颱風清單
    buffer_kilometers: 計算範圍（海岸線外擴公里）。未提供時用 configuration.buffer_kilometers。
    use_rainfall    : 是否納入降水訊號（覆寫 configuration.use_rainfall）
    query_rainfall  : 查詢颱風的事件降水量 (mm)，use_rainfall=True 時用於降水排名
    reference       : 可選；已備妥的參考特徵快取（同範圍可重複使用以省略重算）

    回傳
    ----
    dict：predicted / confidence / votes / ids / table / buffer_kilometers / query_dataframe / reference
    """
    if buffer_kilometers is None:
        buffer_kilometers = configuration.buffer_kilometers
    if reference is None or reference.buffer_kilometers != buffer_kilometers:
        reference = prepare_reference(records, configuration, buffer_kilometers)

    track_dataframe = to_track_dataframe(query_track)
    prediction_result = predict(
        reference,
        track_dataframe,
        configuration,
        neighbor_count=configuration.neighbor_count,
        query_rainfall=query_rainfall,
        use_rainfall=use_rainfall,
    )
    prediction_result["table"] = similar_typhoons_table(
        reference, track_dataframe, prediction_result, buffer_kilometers, configuration
    )
    prediction_result["buffer_kilometers"] = buffer_kilometers
    prediction_result["query_dataframe"] = track_dataframe
    prediction_result["reference"] = reference
    return prediction_result


def predict_typhoons_over_buffers(
    configuration: Configuration,
    query_track,
    records,
    buffer_kilometers_list=None,
    use_rainfall=None,
    query_rainfall=None,
):
    """【主入口・範圍掃描】輸入一組計算範圍，對每個範圍各得到一組預測結果。

    回傳與 buffer_kilometers_list 等長的 list；預設用 configuration.buffer_sweep_kilometers。
    """
    if buffer_kilometers_list is None:
        buffer_kilometers_list = configuration.buffer_sweep_kilometers
    return [
        predict_typhoons(
            configuration,
            query_track,
            records,
            buffer_kilometers=buffer_kilometers,
            use_rainfall=use_rainfall,
            query_rainfall=query_rainfall,
        )
        for buffer_kilometers in buffer_kilometers_list
    ]


def leave_one_out_accuracy(records, configuration: Configuration, buffer_kilometers=None, valid_categories=None):
    """留一驗證：以其餘颱風為參考預測每筆颱風，回報指定計算範圍下的準確率。"""
    if valid_categories is None:
        valid_categories = set("123456789")
    reference = prepare_reference(records, configuration, buffer_kilometers)
    correct = total = 0
    for record in records:
        if record["category"] not in valid_categories:
            continue
        prediction_result = predict(
            reference,
            record["track"],
            configuration,
            neighbor_count=configuration.neighbor_count,
            query_rainfall=(
                record["rainfall"].get(configuration.rainfall_region)
                if configuration.use_rainfall
                else None
            ),
            excluded_id=record["id"],
        )
        total += 1
        correct += prediction_result["predicted"] == record["category"]
    return correct, total
