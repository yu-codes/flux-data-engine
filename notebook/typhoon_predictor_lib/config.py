"""參數配置物件：以 dataclass 集中存放所有「使用者可調整的變數」與其預設值。

主腳本可於最上方直接以 Configuration(...) 覆寫所需參數；演算法常數（台灣海岸線輪廓、
投影參考點、地球半徑等）屬演算法本身，定義於 geometry.py / features.py，不在此處。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Configuration:
    """coastline_rrf 預測所需的全部可調參數（含預設值）。"""

    # 核心輸入：計算範圍（台灣海岸線向外擴張的公里數）
    buffer_kilometers: int = 500

    # 相似度 / 投票
    neighbor_count: int = 5  # 取最相似的前幾條颱風
    use_rainfall: bool = False  # 是否納入事件降水相似度（可選訊號）
    rainfall_region: str = "tn"  # 降水地區：tn=臺南, kh=高雄

    # RRF 融合權重與參數
    coastline_weight: float = 0.80  # 海岸線（絕對位置）訊號權重
    knn_weight: float = 0.20  # KNN 特徵相似度訊號權重
    rainfall_weight: float = 0.08  # 降水訊號權重（use_rainfall=True 時生效）
    rrf_constant: int = 60  # RRF 平滑常數
    pool_factor: int = 12  # 候選池大小 = neighbor_count * pool_factor

    # 顯示 / 評分：相似度 = exp(-偏離公里 / score_scale_kilometers)
    score_scale_kilometers: float = 150.0

    # KNN 特徵權重（對應 extract_features 輸出的 11 維特徵順序）
    feature_weights: list = field(
        default_factory=lambda: [3.0, 2.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.5, 0.5, 0.5]
    )

    # 參考颱風資料檔路徑（相對路徑會由主腳本依所在位置自動尋找）
    data_path: str = "data/typhoon/preprocessed/typhoons_overview.json"

    # 範圍掃描：一次取得多個計算範圍下的預測結果
    buffer_sweep_kilometers: list = field(default_factory=lambda: [300, 500, 800])

    @property
    def candidate_pool_size(self) -> int:
        """候選池大小 = neighbor_count * pool_factor。"""
        return self.neighbor_count * self.pool_factor

    @property
    def feature_weights_array(self) -> np.ndarray:
        return np.asarray(self.feature_weights, float)
