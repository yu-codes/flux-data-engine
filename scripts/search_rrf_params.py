"""
RRF 融合參數快速搜索

策略：先預計算 KNN、DTW、Rule-Based 排名，再快速掃描 RRF 權重組合
這避免了重複計算相似度的開銷
"""

import sys
import json
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.stage00_data_ingestion.typhoon.loader import DataLoader
from src.stage04_feature_engineering.typhoon.extractor import TyphoonFeatureExtractor
from src.stage05_model_training.typhoon.similarity.knn import KNNSimilarity
from src.stage05_model_training.typhoon.similarity.dtw import DTWSimilarity
from src.stage05_model_training.typhoon.similarity.rule_based import classify_typhoon_by_rules
from src.stage05_model_training.typhoon.analog import AnalogModel
from src.stage05_model_training.typhoon.mapping import ImpactMapper


def _setup_chinese_font():
    candidates = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
    for font_name in candidates:
        try:
            fm.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


_setup_chinese_font()

OUTPUT_DIR = ROOT_DIR / "experiments" / "typhoon" / "analysis" / "methods"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

VALID_CATEGORIES = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]


def main():
    print("=" * 60)
    print("RRF 融合參數快速搜索")
    print("=" * 60)

    # 1. 載入資料
    loader = DataLoader("data/typhoon/preprocessed")
    loader.load()
    extractor = TyphoonFeatureExtractor(impact_radius_km=500.0)
    features = extractor.extract_all(loader)
    label_dict = ImpactMapper.build_label_dict(loader)
    model = AnalogModel(label_dict=label_dict)

    all_ids = loader.get_all_ids()
    eval_ids = [tid for tid in all_ids if label_dict.get(tid) in VALID_CATEGORIES]
    print(f"  評估樣本: {len(eval_ids)} 筆")

    # 2. 預計算各方法排名
    print("\n  預計算 KNN 排名...")
    knn = KNNSimilarity()
    knn.fit(features)

    print("  預計算 DTW 排名...")
    dtw = DTWSimilarity(dtw_weights=np.array([1.0, 0.5, 1.0, 0.5]))
    dtw.fit(features)

    print("  預計算 Rule-Based 分類...")
    rule_categories = {}
    for rec in loader.records:
        result = classify_typhoon_by_rules(rec.track, rec.landfall_location)
        rule_categories[rec.typhoon_id] = result["predicted_category"]

    # 3. 預計算完整排名表
    pool_size = len(eval_ids) - 1
    print(f"\n  預計算所有排名（pool={pool_size}）...")

    knn_rankings = {}  # tid -> {other_tid: rank}
    dtw_rankings = {}
    rule_rankings = {}

    for i, tid in enumerate(eval_ids):
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(eval_ids)}...")

        # KNN ranking
        knn_result = knn.find_similar(tid, k=pool_size)
        knn_rankings[tid] = {
            other: rank for rank, other in enumerate(knn_result.similar_ids)
        }

        # DTW ranking
        dtw_result = dtw.find_similar(tid, k=pool_size)
        dtw_rankings[tid] = {
            other: rank for rank, other in enumerate(dtw_result.similar_ids)
        }

        # Rule-based ranking: same category first
        query_cat = rule_categories.get(tid, "")
        same_cat = [
            t for t in eval_ids if t != tid and rule_categories.get(t) == query_cat
        ]
        diff_cat = [
            t for t in eval_ids if t != tid and rule_categories.get(t) != query_cat
        ]
        rule_ranking = {}
        for rank, t in enumerate(same_cat):
            rule_ranking[t] = rank
        for rank, t in enumerate(diff_cat, len(same_cat)):
            rule_ranking[t] = rank
        rule_rankings[tid] = rule_ranking

    # 4. 快速 RRF 權重搜索
    print("\n  開始 RRF 權重搜索...")

    alphas = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
    rule_weights = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70]
    rrf_ks = [30, 60, 90]
    k_vals = [5]

    results = []

    for rrf_k in rrf_ks:
        for alpha in alphas:
            for rw in rule_weights:
                w_dtw = 1.0 - alpha - rw
                if w_dtw < 0.01:
                    continue

                correct = 0
                total = 0

                for tid in eval_ids:
                    # Compute RRF scores
                    candidates = set(
                        list(knn_rankings[tid].keys())[:50]
                        + list(dtw_rankings[tid].keys())[:50]
                    )
                    # Add same-category typhoons
                    query_cat = rule_categories.get(tid, "")
                    same_cat_tids = [
                        t
                        for t in eval_ids
                        if t != tid and rule_categories.get(t) == query_cat
                    ]
                    candidates |= set(same_cat_tids[:50])

                    scores = {}
                    for other in candidates:
                        knn_rank = knn_rankings[tid].get(other, pool_size)
                        dtw_rank = dtw_rankings[tid].get(other, pool_size)
                        rule_rank = rule_rankings[tid].get(other, pool_size)
                        rrf_score = (
                            alpha / (rrf_k + knn_rank)
                            + w_dtw / (rrf_k + dtw_rank)
                            + rw / (rrf_k + rule_rank)
                        )
                        scores[other] = rrf_score

                    # Top-5 prediction
                    top5 = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
                    top5_ids = [t[0] for t in top5]
                    top5_dists = [1.0 - t[1] for t in top5]

                    pred = model.predict(tid, top5_ids, top5_dists)
                    if pred["predicted_category"] == label_dict.get(tid):
                        correct += 1
                    total += 1

                acc = correct / total if total > 0 else 0
                results.append(
                    {
                        "alpha": alpha,
                        "rule_weight": rw,
                        "dtw_weight": round(w_dtw, 2),
                        "rrf_k": rrf_k,
                        "accuracy": acc,
                        "correct": correct,
                        "total": total,
                    }
                )

        print(f"    rrf_k={rrf_k} 完成")

    # 排序
    results.sort(key=lambda x: x["accuracy"], reverse=True)

    print(
        f"\n  最佳參數: alpha={results[0]['alpha']}, rule_weight={results[0]['rule_weight']}, "
        f"dtw_weight={results[0]['dtw_weight']}, rrf_k={results[0]['rrf_k']}"
    )
    print(
        f"  最佳準確率: {results[0]['accuracy']:.3f} ({results[0]['correct']}/{results[0]['total']})"
    )

    print("\n  Top 15:")
    for i, r in enumerate(results[:15]):
        print(
            f"    {i+1}. KNN={r['alpha']:.2f} DTW={r['dtw_weight']:.2f} Rule={r['rule_weight']:.2f} "
            f"rrf_k={r['rrf_k']} → {r['accuracy']:.3f} ({r['correct']}/{r['total']})"
        )

    # 5. 繪製圖表
    best_rrf_k = results[0]["rrf_k"]

    # Heatmap
    heatmap_data = {}
    for r in results:
        if r["rrf_k"] == best_rrf_k:
            heatmap_data[(r["alpha"], r["rule_weight"])] = r["accuracy"]

    unique_alphas = sorted(set(r["alpha"] for r in results if r["rrf_k"] == best_rrf_k))
    unique_rws = sorted(
        set(r["rule_weight"] for r in results if r["rrf_k"] == best_rrf_k)
    )

    heatmap = np.zeros((len(unique_rws), len(unique_alphas)))
    for i, rw in enumerate(unique_rws):
        for j, a in enumerate(unique_alphas):
            heatmap[i, j] = heatmap_data.get((a, rw), 0)

    fig, axes = plt.subplots(1, 2, figsize=(16, 6))

    im = axes[0].imshow(heatmap, cmap="YlOrRd", aspect="auto", origin="lower")
    axes[0].set_xticks(range(len(unique_alphas)))
    axes[0].set_yticks(range(len(unique_rws)))
    axes[0].set_xticklabels([f"{a:.2f}" for a in unique_alphas])
    axes[0].set_yticklabels([f"{rw:.2f}" for rw in unique_rws])
    axes[0].set_xlabel("alpha (KNN weight)")
    axes[0].set_ylabel("Rule-Based weight")
    axes[0].set_title(
        f"RRF Accuracy Heatmap (rrf_k={best_rrf_k})\nDTW weight = 1 - alpha - rule_weight"
    )
    for i in range(len(unique_rws)):
        for j in range(len(unique_alphas)):
            if heatmap[i, j] > 0:
                axes[0].text(
                    j, i, f"{heatmap[i,j]:.2f}", ha="center", va="center", fontsize=7
                )
    plt.colorbar(im, ax=axes[0], fraction=0.046)

    # Top 20 bar chart
    top20 = results[:20]
    labels = [
        f'K={r["alpha"]:.2f} D={r["dtw_weight"]:.2f} R={r["rule_weight"]:.2f} k={r["rrf_k"]}'
        for r in top20
    ]
    accs = [r["accuracy"] for r in top20]
    colors = ["green" if i == 0 else "steelblue" for i in range(len(top20))]

    axes[1].barh(range(len(top20)), accs, color=colors, alpha=0.8)
    axes[1].set_yticks(range(len(top20)))
    axes[1].set_yticklabels(labels, fontsize=7)
    axes[1].set_xlabel("Accuracy")
    axes[1].set_title("Top 20 RRF Parameter Combinations\n(K=KNN, D=DTW, R=Rule)")
    for i, acc in enumerate(accs):
        axes[1].text(acc + 0.002, i, f"{acc:.3f}", va="center", fontsize=8)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "rrf_parameter_analysis.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 保存
    with open(OUTPUT_DIR / "rrf_parameter_search.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "best_params": results[0],
                "top_30": results[:30],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"\n  圖表已保存至: {OUTPUT_DIR / 'rrf_parameter_analysis.png'}")
    return results[0]


if __name__ == "__main__":
    main()
