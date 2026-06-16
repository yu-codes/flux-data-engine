"""
Stage 06 — RRF 融合參數搜索

預計算所有方法的排名，然後快速掃描 RRF 權重組合，
找出最佳 α, rule_weight, rrf_k。

輸出：
- rrf_parameter_analysis.png
- rrf_parameter_search.json
"""

import json
import itertools
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from data_pipiline.stage06_hyperparameter_optimization.typhoon.utils import (
    setup_chinese_font,
    load_data,
    OUTPUT_DIR,
    VALID_CATEGORIES,
    KNNSimilarity,
    DTWSimilarity,
    RuleBasedSimilarity,
    AnalogModel,
)

setup_chinese_font()


def search_rrf_params(loader, features, label_dict):
    """RRF 融合權重搜索"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_ids = list(features.keys())
    eval_ids = [tid for tid in all_ids if label_dict.get(tid) in VALID_CATEGORIES]

    # 預計算各方法排名
    print("  預計算 KNN 排名...")
    knn = KNNSimilarity()
    knn.fit(features)

    print("  預計算 DTW 排名...")
    dtw = DTWSimilarity(dtw_weights=np.array([1.0, 0.5, 1.0, 0.5]))
    dtw.fit(features, loader=loader)

    print("  預計算 Rule 排名...")
    rule = RuleBasedSimilarity()
    rule.fit(features, loader=loader)

    # 預計算所有排名
    knn_ranks = {}
    dtw_ranks = {}
    rule_ranks = {}

    for tid in eval_ids:
        knn_result = knn.find_similar(tid, k=len(all_ids) - 1)
        knn_ranks[tid] = {
            sid: rank for rank, sid in enumerate(knn_result.similar_ids, 1)
        }

        dtw_result = dtw.find_similar(tid, k=len(all_ids) - 1)
        dtw_ranks[tid] = {
            sid: rank for rank, sid in enumerate(dtw_result.similar_ids, 1)
        }

        rule_result = rule.find_similar(tid, k=len(all_ids) - 1)
        rule_ranks[tid] = {
            sid: rank for rank, sid in enumerate(rule_result.similar_ids, 1)
        }

    # 網格搜索
    print("  搜索 RRF 參數...")
    alpha_range = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
    rule_weight_range = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]
    rrf_k_range = [10, 20, 30, 60, 100]

    results = []
    model = AnalogModel(label_dict=label_dict)

    for alpha, rw, rrf_k in itertools.product(
        alpha_range, rule_weight_range, rrf_k_range
    ):
        dtw_w = 1.0 - alpha - rw
        if dtw_w < 0:
            continue

        correct = 0
        for tid in eval_ids:
            # RRF fusion
            candidates = set(
                list(knn_ranks[tid].keys())[:50]
                + list(dtw_ranks[tid].keys())[:50]
                + list(rule_ranks[tid].keys())[:50]
            )
            scores = {}
            for cid in candidates:
                s = 0
                if cid in knn_ranks[tid]:
                    s += alpha / (rrf_k + knn_ranks[tid][cid])
                if cid in dtw_ranks[tid]:
                    s += dtw_w / (rrf_k + dtw_ranks[tid][cid])
                if cid in rule_ranks[tid]:
                    s += rw / (rrf_k + rule_ranks[tid][cid])
                scores[cid] = s

            top_k = sorted(scores, key=scores.get, reverse=True)[:5]
            pred = model.predict(tid, top_k, [1.0 / scores[t] for t in top_k])
            if pred["predicted_category"] == label_dict[tid]:
                correct += 1

        acc = correct / len(eval_ids)
        results.append(
            {
                "alpha": alpha,
                "rule_weight": rw,
                "dtw_weight": round(dtw_w, 2),
                "rrf_k": rrf_k,
                "accuracy": round(acc, 4),
                "correct": correct,
                "total": len(eval_ids),
            }
        )

    results.sort(key=lambda r: r["accuracy"], reverse=True)

    # 繪圖
    fig, ax = plt.subplots(figsize=(12, 6))
    top_10 = results[:10]
    labels = [f"α={r['alpha']},R={r['rule_weight']},k={r['rrf_k']}" for r in top_10]
    accs = [r["accuracy"] * 100 for r in top_10]
    ax.barh(range(len(labels)), accs, color="#2ecc71")
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("準確率 (%)")
    ax.set_title("RRF 融合參數搜索 Top-10")
    for i, v in enumerate(accs):
        ax.text(v + 0.2, i, f"{v:.1f}%", va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "rrf_parameter_analysis.png", dpi=150, bbox_inches="tight")
    plt.close(fig)

    with open(OUTPUT_DIR / "rrf_parameter_search.json", "w", encoding="utf-8") as f:
        json.dump(
            {"results": results[:20], "best": results[0]},
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        f"\n  最佳：α={results[0]['alpha']}, rule={results[0]['rule_weight']}, "
        f"dtw={results[0]['dtw_weight']}, rrf_k={results[0]['rrf_k']} → {results[0]['accuracy']:.1%}"
    )


if __name__ == "__main__":
    print("=" * 60)
    print("RRF 融合參數搜索")
    print("=" * 60)
    loader, features, label_dict, _ = load_data()
    search_rrf_params(loader, features, label_dict)
    print("\n✅ 完成")
