from pathlib import Path

from typhoon_predictor_lib import (
    Configuration,
    leave_one_out_accuracy,
    load_records,
    plot_prediction,
    predict_typhoons,
    predict_typhoons_over_buffers,
)

# 1. 參數設定

configuration = Configuration(
    buffer_kilometers=500,  # 計算範圍：海岸線外擴公里數
    neighbor_count=5,  # 取最相似的前幾條颱風
    use_rainfall=False,  # 是否納入降水訊號
    rainfall_region="tn",  # 降水地區：tn=臺南, kh=高雄
    buffer_sweep_kilometers=[300, 500, 800],  # 多範圍掃描用
)
print(
    "設定完成 | 計算範圍: 海岸線外擴",
    configuration.buffer_kilometers,
    "km | neighbor_count =",
    configuration.neighbor_count,
)


# 2. 載入參考資料

base_directory = (
    Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
)
records, categories_by_id = load_records(
    configuration.data_path, base_directory=base_directory
)
print("已載入", len(records), "筆參考颱風")


# 3. 查詢颱風路徑

query_track = [
    {"latitude": 15.0, "longitude": 135.0, "wind_kt": 25, "pressure_mb": 1002},
    {"latitude": 18.0, "longitude": 129.0, "wind_kt": 50, "pressure_mb": 980},
    {"latitude": 21.0, "longitude": 124.0, "wind_kt": 75, "pressure_mb": 955},
    {"latitude": 22.5, "longitude": 122.0, "wind_kt": 80, "pressure_mb": 950},
    {"latitude": 24.0, "longitude": 120.5, "wind_kt": 70, "pressure_mb": 960},
    {"latitude": 25.5, "longitude": 119.5, "wind_kt": 55, "pressure_mb": 975},
    {"latitude": 27.0, "longitude": 119.0, "wind_kt": 40, "pressure_mb": 990},
]


# 4. 使用案例

# 案例 (i)：計算範圍 500km

prediction_500km = predict_typhoons(
    configuration, query_track, records, buffer_kilometers=500
)
print(
    f"[500km] 預測類型 {prediction_500km['predicted']}"
    f"（信心度 {prediction_500km['confidence'] * 100:.1f}%）"
)
prediction_500km["table"]

# 案例 (ii)：計算範圍 500km，並納入降水訊號

prediction_500km_rainfall = predict_typhoons(
    configuration,
    query_track,
    records,
    buffer_kilometers=500,
    use_rainfall=True,
    query_rainfall=350,  # 查詢颱風預期事件降水量 (mm)
)
print(
    f"[500km+降水] 預測類型 {prediction_500km_rainfall['predicted']}"
    f"（信心度 {prediction_500km_rainfall['confidence'] * 100:.1f}%，"
    f"降水地區 {configuration.rainfall_region}）"
)
prediction_500km_rainfall["table"]

# 案例 (iii)：計算範圍 2000km

prediction_2000km = predict_typhoons(
    configuration, query_track, records, buffer_kilometers=2000
)
print(
    f"[2000km] 預測類型 {prediction_2000km['predicted']}"
    f"（信心度 {prediction_2000km['confidence'] * 100:.1f}%）"
)
prediction_2000km["table"]


# 地圖視覺化（以案例 (i) 為例）

plot_prediction(
    prediction_500km["reference"],
    prediction_500km["query_dataframe"],
    prediction_500km,
    prediction_500km["buffer_kilometers"],
)
