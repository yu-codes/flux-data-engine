"""
Stage 06 — KNN 特徵相關性分析

分析 11 維特徵與降水量的相關性（Pearson + Spearman），
篩選顯著特徵用於 KNN 優化版。

輸出：
- knn_feature_correlation.png
- knn_feature_scatter.png
- knn_correlation_matrix.png
- knn_feature_analysis.json
"""

from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from scipy import stats

from src.stage06_hyperparameter_optimization.typhoon.utils import (
    setup_chinese_font,
    load_data,
    OUTPUT_DIR,
)
import json

setup_chinese_font()


def analyze_knn_features(features: dict, rainfall: dict):
    """KNN 特徵 vs 降水相關性分析"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    feature_names = [
        "min_distance_to_taiwan",
        "mean_angle",
        "max_wind_kt",
        "max_wind_in_window_kt",
        "approach_speed_kmh",
        "min_pressure_mb",
        "intensification_rate",
        "rain_proxy",
        "is_landfall",
        "birth_lon",
        "birth_lat",
    ]

    # 收集有降水資料的颱風
    X, Y = [], []
    for tid, feat in features.items():
        if tid in rainfall and rainfall[tid]["tainan"] > 0:
            X.append(feat.to_feature_vector())
            Y.append(rainfall[tid]["tainan"])
    X = np.array(X)
    Y = np.array(Y)
    print(f"  有降水資料的颱風：{len(X)} 筆")

    # 計算相關性
    results = []
    for i, name in enumerate(feature_names):
        pearson_r, pearson_p = stats.pearsonr(X[:, i], Y)
        spearman_r, spearman_p = stats.spearmanr(X[:, i], Y)
        results.append(
            {
                "feature": name,
                "index": i,
                "pearson_r": round(pearson_r, 4),
                "pearson_p": round(pearson_p, 6),
                "spearman_r": round(spearman_r, 4),
                "spearman_p": round(spearman_p, 6),
            }
        )
    results.sort(key=lambda r: abs(r["spearman_r"]), reverse=True)

    # 篩選顯著特徵
    significant = [r for r in results if r["spearman_p"] < 0.01]
    selected_indices = [r["index"] for r in significant]

    # 繪圖
    fig, ax = plt.subplots(figsize=(12, 6))
    names = [r["feature"] for r in results]
    spearman_vals = [r["spearman_r"] for r in results]
    colors = ["#e74c3c" if r["spearman_p"] < 0.01 else "#95a5a6" for r in results]
    bars = ax.barh(range(len(names)), spearman_vals, color=colors)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    ax.set_xlabel("Spearman ρ (vs 降水量)")
    ax.set_title("KNN 特徵 vs 臺南降水量 相關性分析")
    ax.axvline(x=0, color="black", linewidth=0.5)
    ax.legend(
        [
            plt.Rectangle((0, 0), 1, 1, fc="#e74c3c"),
            plt.Rectangle((0, 0), 1, 1, fc="#95a5a6"),
        ],
        ["顯著 (p<0.01)", "不顯著"],
        loc="lower right",
    )
    fig.tight_layout()
    fig.savefig(
        OUTPUT_DIR / "knn_feature_correlation.png", dpi=150, bbox_inches="tight"
    )
    plt.close(fig)

    # 儲存結果
    output = {
        "all_features": results,
        "significant_features": significant,
        "selected_indices": selected_indices,
        "n_samples": len(X),
    }
    with open(OUTPUT_DIR / "knn_feature_analysis.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  顯著特徵 ({len(significant)}):")
    for r in significant:
        print(f"    {r['feature']}: ρ={r['spearman_r']:.3f}, p={r['spearman_p']:.1e}")


if __name__ == "__main__":
    print("=" * 60)
    print("KNN 特徵相關性分析")
    print("=" * 60)
    _, features, _, rainfall = load_data()
    analyze_knn_features(features, rainfall)
    print("\n✅ 完成")
