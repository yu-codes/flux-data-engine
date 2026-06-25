"""🌀 颱風路徑類比預測套件（coastline_rrf）

模組分類：
  - config        : 參數配置物件 Configuration（含所有可調變數預設值）
  - geometry      : 台灣海岸線幾何、投影、外擴緩衝範圍
  - data_loader   : 載入 typhoons_overview.json
  - features      : 11 維特徵擷取
  - similarity    : Chamfer / KNN / RRF 融合與投票（純算法函數，無「模型」）
  - visualization : 地圖視覺化
  - pipeline      : 高階流程與★主預測入口函數

主入口（輸入參考資料/範圍/查詢路徑 → 得到數個預測颱風結果）：
  pipeline.predict_typhoons()              單一計算範圍
  pipeline.predict_typhoons_over_buffers() 多個計算範圍掃描

備註：類比預測為非參數方法，沒有需要訓練的模型。
prepare_reference() 僅產生「參考特徵快取」（純資料），供算法函數重複取用。
"""

from .config import Configuration
from .data_loader import load_records, resolve_data_path
from .features import extract_features
from .geometry import buffer_polygon, clip_mask, distances_to_coast_kilometers
from .pipeline import (
    leave_one_out_accuracy,
    predict_typhoons,
    predict_typhoons_over_buffers,
    similar_typhoons_table,
    to_track_dataframe,
)
from .similarity import (
    ReferenceIndex,
    chamfer_kilometers,
    fuse_rankings,
    path_offset_kilometers,
    predict,
    prepare_reference,
    rank_coastline,
    rank_knn,
    rank_rainfall,
    vote,
)
from .visualization import plot_prediction

__all__ = [
    "Configuration",
    "load_records",
    "resolve_data_path",
    "extract_features",
    "buffer_polygon",
    "clip_mask",
    "distances_to_coast_kilometers",
    # 純算法函數（無模型）
    "ReferenceIndex",
    "prepare_reference",
    "rank_knn",
    "rank_coastline",
    "rank_rainfall",
    "fuse_rankings",
    "vote",
    "predict",
    "chamfer_kilometers",
    "path_offset_kilometers",
    "plot_prediction",
    # 高階入口
    "to_track_dataframe",
    "similar_typhoons_table",
    "leave_one_out_accuracy",
    "predict_typhoons",
    "predict_typhoons_over_buffers",
]
