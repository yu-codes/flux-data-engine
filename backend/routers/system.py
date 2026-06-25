"""系統層級端點"""

from fastapi import APIRouter

from backend.state import app_state, SUPPORTED_METHODS

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "models_loaded": list(app_state.pipelines.keys()),
    }


@router.get("/api/methods")
def list_methods():
    """列出所有支援的預測方法"""
    return {
        "methods": [
            {
                "id": "coastline_rrf",
                "name": "海岸線 RRF 融合（絕對位置＋KNN＋降水）",
                "description": "以絕對位置相似度為主（權重 0.8），用 RRF 融合 KNN 排名與可選的降水排名做三訊號投票；計算範圍同為海岸線外擴 buffer_km。準確率最高。",
                "accuracy": "82.3%",
            },
            {
                "id": "coastline",
                "name": "海岸線範圍 絕對位置相似度",
                "description": "將台灣海岸線向外擴張 n km，只在此範圍內以絕對經緯度位置比對路徑曲線（Chamfer 距離），找出地圖上最貼近的歷史颱風",
                "accuracy": "73.2%",
            },
            {
                "id": "combined_rainfall",
                "name": "Combined RRF（可選降水訊號）",
                "description": "融合 KNN + DTW + Rule-Based 最佳化參數 (α=0.1, Rule=0.4, DTW=0.5, rrf_k=30)，可選擇納入事件降水相似度",
                "accuracy": "79.8%",
            },
            {
                "id": "knn_optimized",
                "name": "KNN 優化版",
                "description": "只對 3 個顯著特徵加權的 KNN (min_distance, mean_angle, is_landfall)",
                "accuracy": "63.6%",
            },
            {
                "id": "rule_based",
                "name": "Rule-Based 幾何分類",
                "description": "基於路徑幾何特徵的規則式分類器",
                "accuracy": "79.8%",
            },
        ],
        "note": "所有方法皆可設定『海岸線外擴 buffer_km』作為計算範圍（預設 500km）。",
    }
