"""
實驗 006：Coastline 絕對位置相似度（海岸線外擴範圍內路徑比對）

方法理念：
  將台灣海岸線向外擴張 buffer_km，只在此範圍內以「絕對經緯度位置」比對路徑曲線
  （對稱平均最近點 / Chamfer 距離，單位 km），找出地圖上最貼近的歷史颱風路徑。

參數配置：
  - buffer_km: 500（海岸線外擴半徑）
  - k: 5
  - impact_radius_km: 500.0（特徵提取沿用，不影響本方法核心）

評估方式：Leave-One-Out Cross Validation (Cat 1-9)
含降水分析（與其他實驗一致，供詳情頁完整呈現）

執行：python experiments/typhoon/all_cases/exp006_coastline/run.py
"""

import sys
import json
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

import yaml
from data_pipiline.stage09_inference_pipeline.typhoon.predict import (
    DisasterImpactPipeline,
)
from data_pipiline.stage08_downstream_analysis.typhoon.plots import TyphoonVisualizer
from data_pipiline.stage08_downstream_analysis.typhoon.rainfall import RainfallAnalyzer

# === 實驗配置（完整記錄，供未來重現）===
EXPERIMENT_CONFIG = {
    "name": "exp006_coastline",
    "description": "Coastline 絕對位置相似度：海岸線外擴 500km 範圍內，以絕對經緯度位置比對路徑曲線（Chamfer 距離）",
    "method": "coastline",
    "parameters": {
        "buffer_km": 500.0,
        "k": 5,
        "impact_radius_km": 500.0,
    },
    "evaluation": {
        "metrics": ["category_accuracy", "rainfall_analysis"],
        "leave_one_out": True,
        "categories": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
    },
}

EXP_DIR = Path(__file__).parent
PROCESSED_DIR = str(ROOT_DIR / "data" / "typhoon" / "preprocessed")


def get_fixed_example_ids(loader, valid_categories: list[str]) -> dict[str, str]:
    """為每個分類選一個固定的範例颱風 ID"""
    by_cat: dict[str, list[str]] = {}
    for rec in loader.records:
        cat = rec.taiwan_track_category
        if cat in valid_categories:
            by_cat.setdefault(cat, []).append(rec.typhoon_id)
    return {cat: sorted(ids)[0] for cat, ids in by_cat.items()}


def main():
    print("=" * 60)
    print("[EXP] 006: Coastline 絕對位置相似度")
    print("=" * 60)
    print(f"  方法: {EXPERIMENT_CONFIG['method']}")
    print(
        f"  參數: buffer_km={EXPERIMENT_CONFIG['parameters']['buffer_km']}, "
        f"k={EXPERIMENT_CONFIG['parameters']['k']}"
    )
    print()

    # 1. 建立 Pipeline
    pipeline = DisasterImpactPipeline(config=EXPERIMENT_CONFIG)
    pipeline.initialize(PROCESSED_DIR)

    # 2. 評估（Leave-One-Out）
    eval_result = pipeline.evaluate(verbose=True)

    # 3. 建立輸出目錄
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EXP_DIR / "predictions"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 4. 儲存結果
    pipeline.save_results(eval_result, str(run_dir))

    # 5. 組 run_meta
    meta = {
        "timestamp": timestamp,
        "experiment": EXPERIMENT_CONFIG["name"],
        "config_source": "experiments/typhoon/all_cases/exp006_coastline/run.py",
        "method": EXPERIMENT_CONFIG["method"],
        "hazard": "typhoon",
        "parameters": EXPERIMENT_CONFIG["parameters"],
        "results": {
            "accuracy": round(eval_result["accuracy"], 4),
            "total": eval_result["total"],
            "correct": eval_result["correct"],
            "per_category": eval_result["per_category"],
        },
    }

    # 6. 降水分析
    print("\n[RAIN] 執行降水分析...")
    rainfall = RainfallAnalyzer()
    rainfall.load()

    predictions_for_rainfall = []
    for pred in eval_result["predictions"]:
        predictions_for_rainfall.append(
            {
                "typhoon_id": pred.typhoon_id,
                "true_category": pred.true_category,
                "predicted_category": pred.predicted_category,
                "similar_typhoons": pred.similar_typhoons,
            }
        )

    rainfall_eval = rainfall.evaluate_all(predictions_for_rainfall)
    rainfall.generate_plots(rainfall_eval, str(run_dir))

    meta["rainfall"] = {
        "overall_mae": rainfall_eval["overall_mae"],
        "overall_rmse": rainfall_eval["overall_rmse"],
        "count": rainfall_eval["count"],
        "total_with_data": rainfall_eval["total_with_data"],
    }

    with open(run_dir / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 7. 儲存實驗配置
    with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(EXPERIMENT_CONFIG, f, allow_unicode=True, default_flow_style=False)

    # 8. 固定範例
    valid_cats = EXPERIMENT_CONFIG["evaluation"]["categories"]
    fixed_ids = get_fixed_example_ids(pipeline.loader, valid_cats)
    with open(run_dir / "fixed_example_ids.json", "w", encoding="utf-8") as f:
        json.dump(fixed_ids, f, ensure_ascii=False, indent=2)

    # 9. 視覺化（混淆矩陣 / 各類準確率 / 各分類預測範例路徑）
    viz = TyphoonVisualizer(str(run_dir))
    viz.generate_all_prediction_plots(
        eval_result, pipeline.loader, fixed_example_ids=fixed_ids
    )

    # 10. 降水統計圖
    rainfall.generate_category_rainfall_plot(pipeline.loader, str(run_dir))

    # 11. 降水分析詳情
    rainfall_details = []
    for r in rainfall_eval.get("per_prediction", []):
        rainfall_details.append(
            {
                "typhoon_id": r.target_id,
                "target_rainfall": r.target_rainfall,
                "analog_count": len(r.analog_rainfalls),
                "loss_mae": r.loss_mae,
                "loss_rmse": r.loss_rmse,
                "probability_distribution": {
                    station: r.probability_distribution.get(station)
                    for station in ["臺南", "高雄"]
                },
            }
        )
    with open(run_dir / "rainfall_analysis.json", "w", encoding="utf-8") as f:
        json.dump(rainfall_details, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("[DONE] 006 completed!")
    print(
        f"  準確率: {eval_result['accuracy']:.1%} ({eval_result['correct']}/{eval_result['total']})"
    )
    print(f"  結果: {run_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
