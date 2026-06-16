"""
Stage 06 — DTW 參數網格搜索

測試不同四維權重組合 [r, θ, wind, pressure] 的分類準確率，
找出最佳 DTW 參數。

輸出：
- dtw_parameter_search.png
- dtw_parameter_search.json
"""

import json
import random
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from data_pipiline.stage06_hyperparameter_optimization.typhoon.utils import (
    setup_chinese_font,
    load_data,
    OUTPUT_DIR,
    VALID_CATEGORIES,
    DTWSimilarity,
    AnalogModel,
)

setup_chinese_font()


def dtw_parameter_search(loader, features, label_dict):
    """DTW 權重網格搜索"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    weight_candidates = [
        [1.0, 1.0, 1.0, 1.0],
        [1.0, 0.5, 1.0, 0.5],
        [2.0, 1.0, 1.0, 0.5],
        [1.0, 1.0, 1.5, 0.5],
        [1.0, 0.5, 0.5, 0.5],
        [2.0, 0.5, 1.0, 0.3],
        [1.0, 0.8, 1.5, 0.8],
        [1.5, 1.0, 2.0, 0.5],
        [1.0, 1.5, 1.0, 1.0],
        [0.5, 1.0, 1.5, 1.0],
    ]

    all_ids = list(features.keys())
    eval_ids = [tid for tid in all_ids if label_dict.get(tid) in VALID_CATEGORIES]

    # 取樣以加速
    random.seed(42)
    sample_ids = random.sample(eval_ids, min(60, len(eval_ids)))

    results = []
    for weights in weight_candidates:
        print(f"  Testing DTW weights={weights}...")
        dtw_sim = DTWSimilarity(dtw_weights=np.array(weights))
        dtw_sim.fit(features, loader=loader)
        model = AnalogModel(label_dict=label_dict)

        correct = 0
        for tid in sample_ids:
            sim_result = dtw_sim.find_similar(tid, k=5)
            pred = model.predict(tid, sim_result.similar_ids, sim_result.distances)
            if pred["predicted_category"] == label_dict[tid]:
                correct += 1

        acc = correct / len(sample_ids)
        results.append(
            {
                "weights": weights,
                "accuracy": round(acc, 4),
                "correct": correct,
                "total": len(sample_ids),
            }
        )
        print(f"    → accuracy={acc:.1%}")

    results.sort(key=lambda r: r["accuracy"], reverse=True)

    # 繪圖
    fig, ax = plt.subplots(figsize=(10, 6))
    labels = [str(r["weights"]) for r in results]
    accs = [r["accuracy"] * 100 for r in results]
    bars = ax.barh(range(len(labels)), accs, color="#3498db")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("準確率 (%)")
    ax.set_title("DTW 四維權重 [r, θ, wind, pressure] 參數搜索")
    for i, v in enumerate(accs):
        ax.text(v + 0.3, i, f"{v:.1f}%", va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "dtw_parameter_search.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    with open(OUTPUT_DIR / "dtw_parameter_search.json", "w", encoding="utf-8") as f:
        json.dump(
            {"results": results, "best": results[0]}, f, ensure_ascii=False, indent=2
        )

    print(f"\n  最佳：weights={results[0]['weights']} → {results[0]['accuracy']:.1%}")


if __name__ == "__main__":
    print("=" * 60)
    print("DTW 參數網格搜索")
    print("=" * 60)
    loader, features, label_dict, _ = load_data()
    dtw_parameter_search(loader, features, label_dict)
    print("\n✅ 完成")
