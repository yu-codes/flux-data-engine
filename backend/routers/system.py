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
                "id": "combined_optimized",
                "name": "Combined RRF 優化版",
                "description": "融合 KNN + DTW + Rule-Based，使用最佳化參數 (α=0.1, Rule=0.4, DTW=0.5, rrf_k=30)",
                "accuracy": "79.8%",
            },
            {
                "id": "combined",
                "name": "Combined RRF 原版",
                "description": "融合 KNN + DTW + Rule-Based，使用預設參數 (α=0.13, Rule=0.25, rrf_k=60)",
                "accuracy": "74.7%",
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
        ]
    }
