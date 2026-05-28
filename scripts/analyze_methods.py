"""
方法學分析腳本

包含：
1. KNN 特徵 vs 降水相關性分析 → 特徵選擇
2. DTW 參數網格搜索 → 最佳參數
3. Rule-Based 分類準確率嚴格檢核
4. RRF 融合權重敏感度分析

所有分析結果圖表保存至 experiments/typhoon/analysis/methods/
"""

import sys
import json
import csv
import itertools
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from scipy import stats

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.stage00_data_ingestion.typhoon.loader import DataLoader
from src.stage04_feature_engineering.typhoon.extractor import (
    TyphoonFeatureExtractor,
    TyphoonFeatures,
)
from src.stage05_model_training.typhoon.similarity.knn import KNNSimilarity
from src.stage05_model_training.typhoon.similarity.dtw import DTWSimilarity
from src.stage05_model_training.typhoon.similarity.combined import CombinedSimilarity
from src.stage05_model_training.typhoon.similarity.rule_based import (
    classify_typhoon_by_rules,
    RuleBasedSimilarity,
)
from src.stage05_model_training.typhoon.analog import AnalogModel
from src.stage05_model_training.typhoon.mapping import ImpactMapper


# === 中文字型 ===
def _setup_chinese_font():
    candidates = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei", "PingFang TC"]
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


def load_data():
    """載入所有資料"""
    loader = DataLoader("data/typhoon/preprocessed")
    loader.load()
    extractor = TyphoonFeatureExtractor(impact_radius_km=500.0)
    features = extractor.extract_all(loader)
    label_dict = ImpactMapper.build_label_dict(loader)

    # 載入降水資料
    rainfall_path = (
        ROOT_DIR
        / "data"
        / "typhoon"
        / "raw"
        / "typhoon_events_rainfall"
        / "颱風事件雨量.csv"
    )
    rainfall = {}
    with open(rainfall_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = row["颱風編號"]
            tainan = float(row["事件雨量-臺南"]) if row["事件雨量-臺南"] else 0
            kaohsiung = float(row["事件雨量-高雄"]) if row["事件雨量-高雄"] else 0
            rainfall[tid] = (tainan + kaohsiung) / 2.0  # 平均降水作為目標

    return loader, features, label_dict, rainfall


# =============================================================================
# 1. KNN 特徵相關性分析
# =============================================================================


def analyze_knn_features(features: dict, rainfall: dict):
    """
    分析各特徵與降水的相關性 (Pearson & Spearman)
    輸出：相關性矩陣圖、特徵重要性排名
    """
    print("\n" + "=" * 60)
    print("📊 1. KNN 特徵與降水相關性分析")
    print("=" * 60)

    feature_names = TyphoonFeatures.feature_names()

    # 收集有降水資料的颱風特徵
    X_list = []
    y_list = []
    ids_with_data = []

    for tid, feat in features.items():
        if tid in rainfall and rainfall[tid] > 0:
            X_list.append(feat.to_feature_vector())
            y_list.append(rainfall[tid])
            ids_with_data.append(tid)

    X = np.array(X_list)
    y = np.array(y_list)
    print(f"  有降水資料的颱風數量: {len(ids_with_data)}")

    # 計算 Pearson 與 Spearman 相關係數
    pearson_corrs = []
    spearman_corrs = []
    p_values_pearson = []
    p_values_spearman = []

    for i, fname in enumerate(feature_names):
        r_p, p_p = stats.pearsonr(X[:, i], y)
        r_s, p_s = stats.spearmanr(X[:, i], y)
        pearson_corrs.append(r_p)
        spearman_corrs.append(r_s)
        p_values_pearson.append(p_p)
        p_values_spearman.append(p_s)
        sig_p = (
            "***" if p_p < 0.001 else "**" if p_p < 0.01 else "*" if p_p < 0.05 else ""
        )
        sig_s = (
            "***" if p_s < 0.001 else "**" if p_s < 0.01 else "*" if p_s < 0.05 else ""
        )
        print(
            f"  {fname:30s}  Pearson={r_p:+.3f}{sig_p:4s}  Spearman={r_s:+.3f}{sig_s:4s}"
        )

    # 繪製相關性圖
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Bar chart of correlations
    x_pos = np.arange(len(feature_names))
    width = 0.35

    axes[0].barh(
        x_pos - width / 2,
        pearson_corrs,
        width,
        label="Pearson",
        color="steelblue",
        alpha=0.8,
    )
    axes[0].barh(
        x_pos + width / 2,
        spearman_corrs,
        width,
        label="Spearman",
        color="coral",
        alpha=0.8,
    )
    axes[0].set_yticks(x_pos)
    axes[0].set_yticklabels(feature_names, fontsize=9)
    axes[0].set_xlabel("Correlation with Rainfall")
    axes[0].set_title("Feature-Rainfall Correlation Analysis")
    axes[0].legend()
    axes[0].axvline(x=0, color="gray", linestyle="--", alpha=0.5)
    axes[0].axvline(x=0.1, color="green", linestyle=":", alpha=0.5, label="|r|=0.1")
    axes[0].axvline(x=-0.1, color="green", linestyle=":", alpha=0.5)

    # Significance plot
    neg_log_p = [-np.log10(max(p, 1e-30)) for p in p_values_spearman]
    colors = ["green" if p < 0.05 else "gray" for p in p_values_spearman]
    axes[1].barh(x_pos, neg_log_p, color=colors, alpha=0.8)
    axes[1].set_yticks(x_pos)
    axes[1].set_yticklabels(feature_names, fontsize=9)
    axes[1].axvline(x=-np.log10(0.05), color="red", linestyle="--", label="p=0.05")
    axes[1].axvline(x=-np.log10(0.01), color="orange", linestyle="--", label="p=0.01")
    axes[1].set_xlabel("-log10(p-value)")
    axes[1].set_title("Statistical Significance (Spearman)")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(
        OUTPUT_DIR / "knn_feature_correlation.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

    # 特徵與降水的散點圖（Top 6）
    abs_spearman = np.abs(spearman_corrs)
    top6_idx = np.argsort(abs_spearman)[::-1][:6]

    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    for i, idx in enumerate(top6_idx):
        ax = axes[i // 3, i % 3]
        ax.scatter(X[:, idx], y, alpha=0.4, s=20, edgecolors="none")
        ax.set_xlabel(feature_names[idx])
        ax.set_ylabel("Rainfall (mm)")
        r_s = spearman_corrs[idx]
        ax.set_title(f"{feature_names[idx]}\nSpearman r={r_s:.3f}")
        # 加入回歸線
        z = np.polyfit(X[:, idx], y, 1)
        p_line = np.poly1d(z)
        x_range = np.linspace(X[:, idx].min(), X[:, idx].max(), 100)
        ax.plot(x_range, p_line(x_range), "r-", alpha=0.7, linewidth=2)

    plt.suptitle("Top 6 Features vs Rainfall (Scatter)", fontsize=14)
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "knn_feature_scatter.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 特徵間相關性矩陣
    feature_df = pd.DataFrame(X, columns=feature_names)
    feature_df["rainfall"] = y
    corr_matrix = feature_df.corr(method="spearman")

    fig, ax = plt.subplots(figsize=(12, 10))
    im = ax.imshow(corr_matrix.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(corr_matrix.columns)))
    ax.set_yticks(range(len(corr_matrix.columns)))
    ax.set_xticklabels(corr_matrix.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(corr_matrix.columns, fontsize=8)
    for i in range(len(corr_matrix)):
        for j in range(len(corr_matrix)):
            ax.text(
                j,
                i,
                f"{corr_matrix.values[i, j]:.2f}",
                ha="center",
                va="center",
                fontsize=7,
            )
    plt.colorbar(im, ax=ax, fraction=0.046)
    ax.set_title("Feature Correlation Matrix (Spearman)")
    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "knn_correlation_matrix.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 選出相關性 |r| > 0.1 且 p < 0.05 的特徵
    selected_features = []
    selected_indices = []
    for i, fname in enumerate(feature_names):
        if abs(spearman_corrs[i]) >= 0.08 and p_values_spearman[i] < 0.05:
            selected_features.append(fname)
            selected_indices.append(i)

    print(
        f"\n  → 選出的有效特徵 (|Spearman r| >= 0.08, p < 0.05): {len(selected_features)}"
    )
    for i, fname in zip(selected_indices, selected_features):
        print(f"    {fname}: r={spearman_corrs[i]:.3f}, p={p_values_spearman[i]:.4f}")

    # 儲存分析結果
    results = {
        "feature_names": feature_names,
        "pearson_correlations": pearson_corrs,
        "spearman_correlations": spearman_corrs,
        "p_values_spearman": p_values_spearman,
        "selected_features": selected_features,
        "selected_indices": selected_indices,
        "n_samples": len(ids_with_data),
    }
    with open(OUTPUT_DIR / "knn_feature_analysis.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"  圖表已保存至: {OUTPUT_DIR}")
    return selected_indices, spearman_corrs


# =============================================================================
# 2. DTW 參數網格搜索
# =============================================================================


def dtw_parameter_search(loader, features, label_dict):
    """
    對 DTW 參數進行網格搜索：
    - dtw_weights: [w_r, w_theta, w_wind, w_pressure]
    - sakoe_chiba_ratio: 0.1~0.5
    """
    print("\n" + "=" * 60)
    print("📊 2. DTW 參數網格搜索")
    print("=" * 60)

    # 定義搜索空間
    weight_candidates = [
        [1.0, 1.0, 1.0, 0.5],  # baseline
        [1.0, 0.5, 1.0, 0.5],  # reduce theta
        [1.5, 1.0, 1.0, 0.5],  # emphasize distance
        [1.0, 1.0, 1.5, 0.5],  # emphasize wind
        [1.0, 1.0, 1.0, 1.0],  # equal weights
        [2.0, 0.5, 1.0, 0.3],  # distance dominant
        [1.0, 0.8, 1.5, 0.8],  # wind + pressure
        [1.5, 0.8, 1.2, 0.6],  # balanced emphasis on r
        [1.0, 1.0, 2.0, 1.0],  # strong wind
        [2.0, 1.0, 1.0, 0.5],  # strong distance
    ]

    # 只評估部分樣本以加速（每 3 個取 1）
    all_ids = loader.get_all_ids()
    eval_ids = [tid for tid in all_ids if label_dict.get(tid) in VALID_CATEGORIES]
    # 為加速搜索，隨機抽樣 60 筆
    np.random.seed(42)
    if len(eval_ids) > 60:
        sample_ids = list(np.random.choice(eval_ids, 60, replace=False))
    else:
        sample_ids = eval_ids

    results = []
    model = AnalogModel(label_dict=label_dict)

    for wi, weights in enumerate(weight_candidates):
        dtw_sim = DTWSimilarity(dtw_weights=np.array(weights))
        dtw_sim.fit(features)

        correct = 0
        total = 0
        for tid in sample_ids:
            try:
                sim_result = dtw_sim.find_similar(tid, k=5)
                pred = model.predict(tid, sim_result.similar_ids, sim_result.distances)
                if pred["predicted_category"] == label_dict.get(tid):
                    correct += 1
                total += 1
            except Exception:
                continue

        acc = correct / total if total > 0 else 0
        results.append(
            {
                "weights": weights,
                "accuracy": acc,
                "correct": correct,
                "total": total,
            }
        )
        print(f"  weights={weights} → accuracy={acc:.3f} ({correct}/{total})")

    # 排序找最佳
    results.sort(key=lambda x: x["accuracy"], reverse=True)
    best = results[0]
    print(f"\n  → 最佳參數: weights={best['weights']}, accuracy={best['accuracy']:.3f}")

    # 繪製結果
    fig, ax = plt.subplots(figsize=(12, 6))
    labels = [f"w={r['weights']}" for r in results]
    accs = [r["accuracy"] for r in results]
    colors = ["green" if r == results[0] else "steelblue" for r in results]

    bars = ax.barh(range(len(results)), accs, color=colors, alpha=0.8)
    ax.set_yticks(range(len(results)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Accuracy")
    ax.set_title("DTW Parameter Search Results\n[w_r, w_theta, w_wind, w_pressure]")
    for i, (bar, acc) in enumerate(zip(bars, accs)):
        ax.text(acc + 0.005, i, f"{acc:.3f}", va="center", fontsize=9)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "dtw_parameter_search.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 儲存結果
    with open(OUTPUT_DIR / "dtw_parameter_search.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    return best["weights"]


# =============================================================================
# 3. Rule-Based 分類準確率檢核
# =============================================================================


def audit_rule_based(loader, label_dict):
    """
    嚴格檢核 Rule-Based 分類準確率
    目標：達到 80% 以上（理論上應接近 100%）
    """
    print("\n" + "=" * 60)
    print("📊 3. Rule-Based 分類準確率嚴格檢核")
    print("=" * 60)

    correct = 0
    total = 0
    errors = []
    per_cat = {}  # {cat: {"correct": n, "total": n, "errors": [...]}}

    for rec in loader.records:
        true_cat = rec.taiwan_track_category
        if true_cat not in VALID_CATEGORIES:
            continue

        result = classify_typhoon_by_rules(rec.track, rec.landfall_location)
        pred_cat = result["predicted_category"]

        if true_cat not in per_cat:
            per_cat[true_cat] = {"correct": 0, "total": 0, "errors": []}
        per_cat[true_cat]["total"] += 1
        total += 1

        if pred_cat == true_cat:
            correct += 1
            per_cat[true_cat]["correct"] += 1
        else:
            per_cat[true_cat]["errors"].append(
                {
                    "typhoon_id": rec.typhoon_id,
                    "name_zh": rec.name_zh,
                    "year": rec.year,
                    "true": true_cat,
                    "predicted": pred_cat,
                    "reasoning": result["reasoning"],
                    "confidence": result["confidence"],
                    "landfall": rec.landfall_location,
                    "features": result.get("features", {}),
                }
            )
            errors.append(
                {
                    "typhoon_id": rec.typhoon_id,
                    "name_zh": rec.name_zh,
                    "true": true_cat,
                    "predicted": pred_cat,
                }
            )

    accuracy = correct / total if total > 0 else 0
    print(f"\n  整體準確率: {accuracy:.1%} ({correct}/{total})")
    print(f"\n  各類準確率:")
    for cat in sorted(per_cat.keys()):
        info = per_cat[cat]
        cat_acc = info["correct"] / info["total"] if info["total"] > 0 else 0
        print(f"    Cat {cat}: {cat_acc:.1%} ({info['correct']}/{info['total']})")
        if info["errors"]:
            for err in info["errors"][:3]:
                print(
                    f"      ✗ {err['typhoon_id']} ({err['name_zh']}): 預測={err['predicted']}, 原因={err['reasoning'][:50]}"
                )

    # Confusion matrix
    cats = sorted(VALID_CATEGORIES)
    n_cats = len(cats)
    confusion = np.zeros((n_cats, n_cats), dtype=int)
    for rec in loader.records:
        true_cat = rec.taiwan_track_category
        if true_cat not in VALID_CATEGORIES:
            continue
        result = classify_typhoon_by_rules(rec.track, rec.landfall_location)
        pred_cat = result["predicted_category"]
        if pred_cat in cats and true_cat in cats:
            i = cats.index(true_cat)
            j = cats.index(pred_cat)
            confusion[i, j] += 1

    # 繪製混淆矩陣
    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(confusion, cmap="Blues", aspect="auto")
    ax.set_xticks(range(n_cats))
    ax.set_yticks(range(n_cats))
    ax.set_xticklabels([f"Cat {c}" for c in cats])
    ax.set_yticklabels([f"Cat {c}" for c in cats])
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(
        f"Rule-Based Classification Confusion Matrix\nAccuracy: {accuracy:.1%} ({correct}/{total})"
    )

    for i in range(n_cats):
        for j in range(n_cats):
            color = "white" if confusion[i, j] > confusion.max() * 0.5 else "black"
            ax.text(
                j,
                i,
                str(confusion[i, j]),
                ha="center",
                va="center",
                color=color,
                fontsize=10,
            )

    plt.colorbar(im, ax=ax, fraction=0.046)
    plt.tight_layout()
    plt.savefig(
        OUTPUT_DIR / "rule_based_confusion_matrix.png", dpi=150, bbox_inches="tight"
    )
    plt.close()

    # 儲存錯誤分析
    with open(OUTPUT_DIR / "rule_based_errors.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "accuracy": accuracy,
                "correct": correct,
                "total": total,
                "per_category": {
                    cat: {"accuracy": info["correct"] / max(info["total"], 1), **info}
                    for cat, info in per_cat.items()
                },
                "all_errors": errors,
            },
            f,
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    return accuracy, per_cat, errors


# =============================================================================
# 4. RRF 融合參數分析
# =============================================================================


def analyze_rrf_parameters(loader, features, label_dict):
    """
    分析 RRF 各權重比例對準確率的影響
    搜索 alpha (KNN), rule_weight, rrf_k
    """
    print("\n" + "=" * 60)
    print("📊 4. RRF 融合參數敏感度分析")
    print("=" * 60)

    all_ids = loader.get_all_ids()
    eval_ids = [tid for tid in all_ids if label_dict.get(tid) in VALID_CATEGORIES]

    # 抽樣加速
    np.random.seed(42)
    if len(eval_ids) > 60:
        sample_ids = list(np.random.choice(eval_ids, 60, replace=False))
    else:
        sample_ids = eval_ids

    model = AnalogModel(label_dict=label_dict)

    # Grid search: alpha, rule_weight
    alphas = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
    rule_weights = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
    rrf_ks = [30, 60, 90]

    results = []
    best_acc = 0
    best_params = {}

    for rrf_k in rrf_ks:
        for alpha in alphas:
            for rw in rule_weights:
                if alpha + rw > 0.95:
                    continue  # w_dtw would be too small or negative

                sim = CombinedSimilarity(
                    alpha=alpha, rule_weight=rw, rrf_k=rrf_k, pool_size_factor=10
                )
                sim.fit(features, loader=loader)

                correct = 0
                total = 0
                for tid in sample_ids:
                    try:
                        sim_result = sim.find_similar(tid, k=5)
                        pred = model.predict(
                            tid, sim_result.similar_ids, sim_result.distances
                        )
                        if pred["predicted_category"] == label_dict.get(tid):
                            correct += 1
                        total += 1
                    except Exception:
                        continue

                acc = correct / total if total > 0 else 0
                results.append(
                    {
                        "alpha": alpha,
                        "rule_weight": rw,
                        "dtw_weight": round(1 - alpha - rw, 2),
                        "rrf_k": rrf_k,
                        "accuracy": acc,
                        "correct": correct,
                        "total": total,
                    }
                )

                if acc > best_acc:
                    best_acc = acc
                    best_params = {"alpha": alpha, "rule_weight": rw, "rrf_k": rrf_k}

    print(
        f"\n  最佳參數: alpha={best_params['alpha']}, rule_weight={best_params['rule_weight']}, rrf_k={best_params['rrf_k']}"
    )
    print(f"  最佳準確率: {best_acc:.3f}")

    # 排序結果
    results.sort(key=lambda x: x["accuracy"], reverse=True)
    print("\n  Top 10 參數組合:")
    for i, r in enumerate(results[:10]):
        print(
            f"    {i+1}. α={r['alpha']:.2f} rule={r['rule_weight']:.2f} dtw={r['dtw_weight']:.2f} rrf_k={r['rrf_k']} → {r['accuracy']:.3f}"
        )

    # 繪製 heatmap (alpha vs rule_weight, rrf_k=best)
    best_rrf_k = best_params["rrf_k"]
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

    # Heatmap
    im = axes[0].imshow(heatmap, cmap="YlOrRd", aspect="auto", origin="lower")
    axes[0].set_xticks(range(len(unique_alphas)))
    axes[0].set_yticks(range(len(unique_rws)))
    axes[0].set_xticklabels([f"{a:.2f}" for a in unique_alphas])
    axes[0].set_yticklabels([f"{rw:.2f}" for rw in unique_rws])
    axes[0].set_xlabel("α (KNN weight)")
    axes[0].set_ylabel("Rule-Based weight")
    axes[0].set_title(
        f"RRF Accuracy Heatmap (rrf_k={best_rrf_k})\nDTW weight = 1 - α - rule_weight"
    )
    for i in range(len(unique_rws)):
        for j in range(len(unique_alphas)):
            axes[0].text(
                j, i, f"{heatmap[i,j]:.2f}", ha="center", va="center", fontsize=7
            )
    plt.colorbar(im, ax=axes[0], fraction=0.046)

    # Top 15 bar chart
    top15 = results[:15]
    labels = [
        f'α={r["alpha"]:.2f},r={r["rule_weight"]:.2f},d={r["dtw_weight"]:.2f}'
        for r in top15
    ]
    accs = [r["accuracy"] for r in top15]
    colors = ["green" if i == 0 else "steelblue" for i in range(len(top15))]

    axes[1].barh(range(len(top15)), accs, color=colors, alpha=0.8)
    axes[1].set_yticks(range(len(top15)))
    axes[1].set_yticklabels(labels, fontsize=8)
    axes[1].set_xlabel("Accuracy")
    axes[1].set_title("Top 15 RRF Parameter Combinations")
    for i, acc in enumerate(accs):
        axes[1].text(acc + 0.002, i, f"{acc:.3f}", va="center", fontsize=8)

    plt.tight_layout()
    plt.savefig(OUTPUT_DIR / "rrf_parameter_analysis.png", dpi=150, bbox_inches="tight")
    plt.close()

    # 儲存
    with open(OUTPUT_DIR / "rrf_parameter_search.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "best_params": best_params,
                "best_accuracy": best_acc,
                "all_results": results[:30],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    return best_params


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    print("🔬 方法學分析開始")
    print("=" * 60)

    loader, features, label_dict, rainfall = load_data()

    # 1. KNN 特徵分析
    selected_indices, spearman_corrs = analyze_knn_features(features, rainfall)

    # 2. DTW 參數搜索
    best_dtw_weights = dtw_parameter_search(loader, features, label_dict)

    # 3. Rule-Based 檢核
    rb_accuracy, rb_per_cat, rb_errors = audit_rule_based(loader, label_dict)

    # 4. RRF 參數分析
    best_rrf_params = analyze_rrf_parameters(loader, features, label_dict)

    # 彙總
    print("\n" + "=" * 60)
    print("📋 分析彙總")
    print("=" * 60)
    print(f"  KNN 有效特徵: {len(selected_indices)} / 11")
    print(f"  DTW 最佳權重: {best_dtw_weights}")
    print(f"  Rule-Based 準確率: {rb_accuracy:.1%}")
    print(f"  RRF 最佳參數: {best_rrf_params}")
    print(f"\n  所有分析圖表已保存至: {OUTPUT_DIR}")
