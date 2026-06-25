"""
實驗 007：Coastline RRF 融合（絕對位置為主 + KNN + 可選降水）

方法理念：
  以 coastline（海岸線範圍內絕對位置 / Chamfer 距離）為主訊號，
  用 Reciprocal Rank Fusion 融合 KNN 排名與可選的降水排名做三訊號投票，
  兼顧「地圖上最貼近」與「特徵相似」。

最佳化參數（由 LOO 網格搜尋）：
  - w_coastline: 0.80（絕對位置占極高比重）
  - w_knn:       0.20
  - rrf_k:       60
  - pool_size_factor: 12
  - buffer_km:   500（計算範圍：海岸線外擴）

評估方式：Leave-One-Out Cross Validation (Cat 1-9)；含降水分析。

執行：python experiments/typhoon/all_cases/exp007_coastline_rrf/run.py
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

EXPERIMENT_CONFIG = {
    "name": "exp007_coastline_rrf",
    "description": "Coastline RRF 融合：絕對位置(0.80) + KNN(0.20) + 可選降水，RRF rrf_k=60，緩衝 500km",
    "method": "coastline_rrf",
    "parameters": {
        "buffer_km": 500.0,
        "weight_coastline": 0.80,
        "weight_knn": 0.20,
        "rrf_k": 60,
        "pool_size_factor": 12,
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
    by_cat: dict[str, list[str]] = {}
    for rec in loader.records:
        cat = rec.taiwan_track_category
        if cat in valid_categories:
            by_cat.setdefault(cat, []).append(rec.typhoon_id)
    return {cat: sorted(ids)[0] for cat, ids in by_cat.items()}


def main():
    print("=" * 60)
    print("[EXP] 007: Coastline RRF 融合")
    print("=" * 60)
    print(f"  方法: {EXPERIMENT_CONFIG['method']}")
    print(
        f"  參數: w_coastline={EXPERIMENT_CONFIG['parameters']['weight_coastline']}, "
        f"w_knn={EXPERIMENT_CONFIG['parameters']['weight_knn']}, "
        f"rrf_k={EXPERIMENT_CONFIG['parameters']['rrf_k']}"
    )
    print()

    pipeline = DisasterImpactPipeline(config=EXPERIMENT_CONFIG)
    pipeline.initialize(PROCESSED_DIR)

    eval_result = pipeline.evaluate(verbose=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EXP_DIR / "predictions"
    run_dir.mkdir(parents=True, exist_ok=True)
    pipeline.save_results(eval_result, str(run_dir))

    meta = {
        "timestamp": timestamp,
        "experiment": EXPERIMENT_CONFIG["name"],
        "config_source": "experiments/typhoon/all_cases/exp007_coastline_rrf/run.py",
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

    print("\n[RAIN] 執行降水分析...")
    rainfall = RainfallAnalyzer()
    rainfall.load()
    predictions_for_rainfall = [
        {
            "typhoon_id": pred.typhoon_id,
            "true_category": pred.true_category,
            "predicted_category": pred.predicted_category,
            "similar_typhoons": pred.similar_typhoons,
        }
        for pred in eval_result["predictions"]
    ]
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
    with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(EXPERIMENT_CONFIG, f, allow_unicode=True, default_flow_style=False)

    valid_cats = EXPERIMENT_CONFIG["evaluation"]["categories"]
    fixed_ids = get_fixed_example_ids(pipeline.loader, valid_cats)
    with open(run_dir / "fixed_example_ids.json", "w", encoding="utf-8") as f:
        json.dump(fixed_ids, f, ensure_ascii=False, indent=2)

    viz = TyphoonVisualizer(str(run_dir))
    viz.generate_all_prediction_plots(
        eval_result, pipeline.loader, fixed_example_ids=fixed_ids
    )
    rainfall.generate_category_rainfall_plot(pipeline.loader, str(run_dir))

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
    print("[DONE] 007 completed!")
    print(
        f"  準確率: {eval_result['accuracy']:.1%} ({eval_result['correct']}/{eval_result['total']})"
    )
    print(f"  結果: {run_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
