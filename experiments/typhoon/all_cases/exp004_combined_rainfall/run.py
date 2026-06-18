"""
實驗 004：Combined RRF + 降水訊號（Rainfall-aware）

在優化版（exp003）基礎上，啟用第 4 個 RRF 排名訊號——降水相似度：
依指定地區（rainfall_region）的事件降水量，將降水規模相近的歷史颱風納入排名。

參數配置：
  - method: combined_rainfall
  - alpha=0.10, rule_weight=0.40, rrf_k=30, k=5
  - dtw_weights=[1.0, 0.5, 1.0, 0.5], feature-weighted KNN
  - use_rainfall=True, rainfall_region="tn"(臺南), rainfall_weight=0.15
  - w_dtw = 1 - alpha - rule_weight - rainfall_weight = 0.35

評估方式：Leave-One-Out Cross Validation
資料源：typhoons_overview_198.json（198 筆 Cat 1-9 評估子集，獨立建檔）

執行：python experiments/typhoon/all_cases/exp004_combined_rainfall/run.py
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
from data_pipiline.stage00_data_ingestion.typhoon.regions import region_label

# === 實驗配置 ===
RAINFALL_REGION = "tn"

EXPERIMENT_CONFIG = {
    "name": "exp004_combined_rainfall",
    "description": (
        "Combined RRF + 降水訊號: KNN(0.10) + DTW(0.35) + Rule(0.40) + "
        f"Rainfall[{RAINFALL_REGION}](0.15), rrf_k=30，198 筆評估子集"
    ),
    "method": "combined_rainfall",
    "parameters": {
        "alpha": 0.10,
        "rule_weight": 0.40,
        "k": 5,
        "impact_radius_km": 500.0,
        "pool_size_factor": 10,
        "rrf_k": 30,
        "dtw_weights": [1.0, 0.5, 1.0, 0.5],
        "feature_weights": [3.0, 2.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.5, 0.5, 0.5],
        "use_rainfall": True,
        "rainfall_region": RAINFALL_REGION,
        "rainfall_weight": 0.15,
    },
    "evaluation": {
        "metrics": ["category_accuracy", "rainfall_analysis"],
        "leave_one_out": True,
        "categories": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
    },
}

EXP_DIR = Path(__file__).parent
PROCESSED_DIR = str(ROOT_DIR / "data" / "typhoon" / "preprocessed")
EVAL_DATASET = "typhoons_overview_198.json"  # 198 筆評估子集


def get_fixed_example_ids(loader, valid_categories):
    by_cat = {}
    for rec in loader.records:
        cat = rec.taiwan_track_category
        if cat in valid_categories:
            by_cat.setdefault(cat, []).append(rec.typhoon_id)
    return {cat: sorted(ids)[0] for cat, ids in by_cat.items()}


def main():
    print("=" * 60)
    print("[EXP] 004: Combined RRF + 降水訊號")
    print("=" * 60)
    print(f"  方法: {EXPERIMENT_CONFIG['method']}")
    print(f"  降水地區: {region_label(RAINFALL_REGION)} ({RAINFALL_REGION})")
    print(f"  評估資料: {EVAL_DATASET}")
    print()

    # 1. 建立 Pipeline（使用 198 筆評估子集）
    pipeline = DisasterImpactPipeline(config=EXPERIMENT_CONFIG)
    pipeline.initialize(PROCESSED_DIR, dataset_filename=EVAL_DATASET)

    # 2. 評估
    eval_result = pipeline.evaluate(verbose=True)

    # 3. 輸出目錄
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = EXP_DIR / "predictions"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 4. 儲存結果
    pipeline.save_results(eval_result, str(run_dir))

    # 5. run_meta
    meta = {
        "timestamp": timestamp,
        "experiment": EXPERIMENT_CONFIG["name"],
        "config_source": "experiments/typhoon/all_cases/exp004_combined_rainfall/run.py",
        "method": EXPERIMENT_CONFIG["method"],
        "hazard": "typhoon",
        "dataset": EVAL_DATASET,
        "parameters": EXPERIMENT_CONFIG["parameters"],
        "results": {
            "accuracy": round(eval_result["accuracy"], 4),
            "total": eval_result["total"],
            "correct": eval_result["correct"],
            "per_category": eval_result["per_category"],
        },
    }

    # 6. 降水分析（region-based，共用 pipeline 的 loader）
    print("\n[RAIN] 執行降水分析...")
    rainfall = RainfallAnalyzer(loader=pipeline.loader)
    rainfall.load()

    predictions_for_rainfall = [
        {
            "typhoon_id": p.typhoon_id,
            "true_category": p.true_category,
            "predicted_category": p.predicted_category,
            "similar_typhoons": p.similar_typhoons,
        }
        for p in eval_result["predictions"]
    ]

    rainfall_eval = rainfall.evaluate_all(predictions_for_rainfall)
    rainfall.generate_plots(rainfall_eval, str(run_dir))

    meta["rainfall"] = {
        "regions": rainfall_eval["regions"],
        "overall_mae": rainfall_eval["overall_mae"],
        "overall_rmse": rainfall_eval["overall_rmse"],
        "count": rainfall_eval["count"],
    }

    with open(run_dir / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 7. 實驗配置
    with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
        yaml.dump(EXPERIMENT_CONFIG, f, allow_unicode=True, default_flow_style=False)

    # 8. 固定範例
    valid_cats = EXPERIMENT_CONFIG["evaluation"]["categories"]
    fixed_ids = get_fixed_example_ids(pipeline.loader, valid_cats)
    with open(run_dir / "fixed_example_ids.json", "w", encoding="utf-8") as f:
        json.dump(fixed_ids, f, ensure_ascii=False, indent=2)

    # 9. 視覺化
    viz = TyphoonVisualizer(str(run_dir))
    viz.generate_all_prediction_plots(
        eval_result, pipeline.loader, fixed_example_ids=fixed_ids
    )

    # 10. 降水統計圖
    rainfall.generate_category_rainfall_plot(pipeline.loader, str(run_dir))

    # 11. 降水分析詳情（各地區）
    rainfall_details = []
    for r in rainfall_eval.get("per_prediction", []):
        rainfall_details.append(
            {
                "typhoon_id": r.target_id,
                "target_rainfall": r.target_rainfall,
                "analog_count": len(r.analog_rainfalls),
                "loss_mae": r.loss_mae,
                "loss_rmse": r.loss_rmse,
            }
        )
    with open(run_dir / "rainfall_analysis.json", "w", encoding="utf-8") as f:
        json.dump(rainfall_details, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("[DONE] 004 completed!")
    print(
        f"  準確率: {eval_result['accuracy']:.1%} "
        f"({eval_result['correct']}/{eval_result['total']})"
    )
    mae = rainfall_eval["overall_mae"]
    print(
        "  降水 MAE: "
        + ", ".join(
            f"{region_label(rg)}={mae.get(rg)}" for rg in rainfall_eval["regions"]
        )
    )
    print(f"  結果: {run_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
