"""
Config-Driven 預測執行腳本

每次執行：
  1. 讀取指定的 config YAML
  2. 組裝 pipeline
  3. 執行評估
  4. 儲存結果 + config 副本到 outputs/predictions/{timestamp}/

Usage:
  python scripts/run_prediction.py --method combined --alpha 0.2 --k 5
  python scripts/run_prediction.py --method rule_based --k 5
"""

import sys
import json
import shutil
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from src.stage09_inference_pipeline.typhoon.predict import DisasterImpactPipeline
from src.stage08_downstream_analysis.typhoon.plots import TyphoonVisualizer


def load_config(config_path: str) -> dict:
    """載入 YAML config"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_config_from_args(args) -> dict:
    """從 CLI args 建構 config dict"""
    return {
        "name": f"{args.method}_cli",
        "description": f"CLI run: {args.method}",
        "method": args.method,
        "parameters": {
            "alpha": args.alpha,
            "k": args.k,
            "impact_radius_km": 500.0,
            "pool_size_factor": 10,
            "rrf_k": 60,
        },
        "evaluation": {
            "metrics": ["category_accuracy"],
            "leave_one_out": True,
            "categories": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
        },
    }


def get_fixed_example_ids(loader, valid_categories: list[str]) -> dict[str, str]:
    """為每個分類選一個固定的範例颱風 ID"""
    by_cat: dict[str, list[str]] = {}
    for rec in loader.records:
        cat = rec.taiwan_track_category
        if cat in valid_categories:
            by_cat.setdefault(cat, []).append(rec.typhoon_id)
    return {cat: sorted(ids)[0] for cat, ids in by_cat.items()}


def run_single(pipeline, typhoon_id: str, k: int):
    """單一預測"""
    result = pipeline.predict(typhoon_id, k=k)
    print(f"\n{'='*60}")
    print(f"颱風：{result.name_zh} {result.name_en} ({result.typhoon_id})")
    print(f"真實分類：{result.true_category}")
    print(f"預測分類：{result.predicted_category} (信心度: {result.confidence:.1%})")
    print(f"預測{'✓ 正確' if result.is_correct else '✗ 錯誤'}")
    print(f"\n相似颱風：")
    for st in result.similar_typhoons:
        print(
            f"  - {st['name_zh']} ({st['year']}) "
            f"類型{st['category']} 距離={st['distance']:.3f}"
        )


def run_evaluation(pipeline, config: dict, output_dir: Path, config_path: str = None):
    """完整評估 + 版本控制"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    method_name = config.get("name", config["method"])
    run_dir = output_dir / f"{timestamp}_{method_name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 評估
    eval_result = pipeline.evaluate(verbose=True)

    # 儲存結果
    pipeline.save_results(eval_result, str(run_dir))

    # 儲存完整 run_meta
    meta = {
        "timestamp": timestamp,
        "config_name": config.get("name", "unknown"),
        "config_source": config_path or "cli",
        "method": config["method"],
        "parameters": config.get("parameters", {}),
        "results": {
            "accuracy": round(eval_result["accuracy"], 4),
            "total": eval_result["total"],
            "correct": eval_result["correct"],
            "per_category": eval_result["per_category"],
        },
    }
    with open(run_dir / "run_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 複製 config 到結果目錄
    if config_path and Path(config_path).exists():
        shutil.copy2(config_path, run_dir / "experiment_config.yaml")
    else:
        with open(run_dir / "experiment_config.yaml", "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    # 固定範例
    valid_cats = config.get("evaluation", {}).get(
        "categories", ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    )
    fixed_ids = get_fixed_example_ids(pipeline.loader, valid_cats)
    with open(run_dir / "fixed_example_ids.json", "w", encoding="utf-8") as f:
        json.dump(fixed_ids, f, ensure_ascii=False, indent=2)

    # 視覺化
    viz = TyphoonVisualizer(str(run_dir))
    viz.generate_all_prediction_plots(
        eval_result, pipeline.loader, fixed_example_ids=fixed_ids
    )

    print(f"\n✅ 完成！結果已儲存至 {run_dir}/")
    return run_dir


def main():
    parser = argparse.ArgumentParser(description="颱風類比預測系統 — Config-Driven")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="實驗 config YAML 路徑",
    )
    parser.add_argument(
        "--method",
        type=str,
        default="combined",
        choices=["knn", "dtw", "combined", "baseline", "rule_based"],
        help="相似度計算方法（當未指定 --config 時使用）",
    )
    parser.add_argument("--alpha", type=float, default=0.2)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--typhoon-id", type=str, default=None)
    parser.add_argument("--processed-dir", type=str, default="data/typhoon/preprocessed")
    parser.add_argument("--output-dir", type=str, default="outputs/predictions")
    args = parser.parse_args()

    print("=" * 60)
    print("🌀 颱風類比災害預測系統")
    print("=" * 60)

    # 載入或建構 config
    config_path = args.config
    if config_path:
        config = load_config(config_path)
        print(f"📋 使用配置: {config_path}")
        print(f"   方法: {config['method']}")
        print(f"   參數: {config.get('parameters', {})}")
    else:
        config = build_config_from_args(args)

    # 建立 pipeline
    pipeline = DisasterImpactPipeline(config=config)
    pipeline.initialize(args.processed_dir)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.typhoon_id:
        k = config.get("parameters", {}).get("k", 5)
        run_single(pipeline, args.typhoon_id, k)
    else:
        run_evaluation(pipeline, config, output_dir, config_path)


if __name__ == "__main__":
    main()
