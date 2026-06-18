"""
視覺化模組

提供各種颱風資料和預測結果的圖表
"""

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import seaborn as sns
from pathlib import Path
from typing import Optional

from data_pipiline.stage00_data_ingestion.typhoon.loader import (
    DataLoader,
    TyphoonRecord,
)
from data_pipiline.stage04_feature_engineering.typhoon.extractor import (
    TyphoonFeatures,
    TAIWAN_LAT,
    TAIWAN_LON,
    haversine_vec,
)
from data_pipiline.stage00_data_ingestion.typhoon.regions import (
    region_codes,
    region_label,
)


# 嘗試設定中文字型
def _setup_chinese_font():
    """嘗試設定中文字型"""
    candidates = [
        "Microsoft JhengHei",  # Windows 正黑體
        "Microsoft YaHei",  # Windows 雅黑
        "SimHei",  # 黑體
        "PingFang TC",  # macOS
        "Noto Sans CJK TC",  # Linux
    ]
    for font_name in candidates:
        try:
            fm.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return font_name
        except Exception:
            continue
    # fallback: 不設定中文字型
    return None


_setup_chinese_font()

# 路徑分類的配色方案
CATEGORY_COLORS = {
    "1": "#e41a1c",
    "2": "#377eb8",
    "3": "#4daf4a",
    "4": "#984ea3",
    "5": "#ff7f00",
    "6": "#a65628",
    "7": "#f781bf",
    "8": "#999999",
    "9": "#66c2a5",
    "特殊": "#000000",
}


def _get_color(category: str) -> str:
    return CATEGORY_COLORS.get(str(category), "#333333")


class TyphoonVisualizer:
    """颱風視覺化工具"""

    def __init__(self, output_dir: str = "outputs/figures"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _save(self, fig: plt.Figure, name: str):
        path = self.output_dir / f"{name}.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"  ✓ 已儲存：{path}")

    # ================================================================
    # 1. 資料分析圖
    # ================================================================

    def plot_category_distribution(self, loader: DataLoader):
        """路徑分類長條圖"""
        cats = [r.taiwan_track_category for r in loader.records]
        counter = pd.Series(cats).value_counts().sort_index()

        fig, ax = plt.subplots(figsize=(10, 5))
        bars = ax.bar(
            counter.index, counter.values, color=[_get_color(c) for c in counter.index]
        )
        ax.set_xlabel("侵臺路徑分類")
        ax.set_ylabel("颱風數")
        ax.set_title("侵臺路徑分類分布")
        for bar, val in zip(bars, counter.values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                val + 0.3,
                str(val),
                ha="center",
                va="bottom",
                fontsize=9,
            )
        self._save(fig, "category_distribution")

    def plot_all_tracks(self, loader: DataLoader, by_category: bool = True):
        """所有颱風軌跡圖（依分類著色）"""
        fig, ax = plt.subplots(figsize=(12, 10))

        for rec in loader.records:
            lats = rec.track["latitude"].values
            lons = rec.track["longitude"].values
            color = _get_color(rec.taiwan_track_category) if by_category else "#cccccc"
            ax.plot(lons, lats, color=color, alpha=0.3, linewidth=0.7)

        # 台灣位置
        ax.plot(TAIWAN_LON, TAIWAN_LAT, "r*", markersize=15, zorder=10)
        ax.annotate(
            "台灣", (TAIWAN_LON + 0.5, TAIWAN_LAT + 0.5), fontsize=10, color="red"
        )

        # 500km 圈
        theta = np.linspace(0, 2 * np.pi, 100)
        r_deg = 500 / 111  # 約略轉換
        ax.plot(
            TAIWAN_LON + r_deg * np.cos(theta),
            TAIWAN_LAT + r_deg * np.sin(theta),
            "r--",
            alpha=0.5,
            linewidth=1,
            label="500km",
        )

        ax.set_xlabel("經度 (°E)")
        ax.set_ylabel("緯度 (°N)")
        ax.set_title("所有侵臺颱風軌跡（依路徑分類著色）")
        ax.set_xlim(100, 170)
        ax.set_ylim(0, 45)
        ax.grid(True, alpha=0.3)

        # 圖例
        from matplotlib.lines import Line2D

        legend_items = []
        for cat in sorted(CATEGORY_COLORS.keys()):
            legend_items.append(
                Line2D([0], [0], color=_get_color(cat), lw=2, label=f"類型 {cat}")
            )
        ax.legend(handles=legend_items, loc="upper right", fontsize=8, ncol=2)

        self._save(fig, "all_tracks_by_category")

    def plot_tracks_by_category(self, loader: DataLoader):
        """分類別小圖"""
        categories = sorted(set(r.taiwan_track_category for r in loader.records))
        n_cats = len(categories)
        ncols = 4
        nrows = (n_cats + ncols - 1) // ncols

        fig, axes = plt.subplots(nrows, ncols, figsize=(20, 5 * nrows))
        axes = axes.flatten() if n_cats > 1 else [axes]

        for idx, cat in enumerate(categories):
            ax = axes[idx]
            typhoons = [r for r in loader.records if r.taiwan_track_category == cat]

            for rec in typhoons:
                lats = rec.track["latitude"].values
                lons = rec.track["longitude"].values
                ax.plot(lons, lats, color=_get_color(cat), alpha=0.4, linewidth=0.8)

            ax.plot(TAIWAN_LON, TAIWAN_LAT, "r*", markersize=10)
            ax.set_title(f"類型 {cat} (n={len(typhoons)})")
            ax.set_xlim(100, 170)
            ax.set_ylim(0, 45)
            ax.grid(True, alpha=0.3)

        for idx in range(len(categories), len(axes)):
            axes[idx].set_visible(False)

        fig.suptitle("各路徑分類颱風軌跡", fontsize=14, y=1.01)
        fig.tight_layout()
        self._save(fig, "tracks_by_category")

    def plot_genesis_locations(self, loader: DataLoader):
        """颱風生成位置散布圖"""
        fig, ax = plt.subplots(figsize=(12, 8))

        for rec in loader.records:
            if rec.birth_lon and rec.birth_lat:
                ax.scatter(
                    rec.birth_lon,
                    rec.birth_lat,
                    c=_get_color(rec.taiwan_track_category),
                    s=30,
                    alpha=0.6,
                    edgecolors="none",
                )

        ax.plot(TAIWAN_LON, TAIWAN_LAT, "r*", markersize=15, zorder=10)
        ax.set_xlabel("經度 (°E)")
        ax.set_ylabel("緯度 (°N)")
        ax.set_title("颱風生成位置（依路徑分類著色）")
        ax.grid(True, alpha=0.3)

        from matplotlib.lines import Line2D

        legend_items = []
        for cat in sorted(CATEGORY_COLORS.keys()):
            legend_items.append(
                Line2D(
                    [0],
                    [0],
                    marker="o",
                    color="w",
                    markerfacecolor=_get_color(cat),
                    markersize=8,
                    label=f"類型 {cat}",
                )
            )
        ax.legend(handles=legend_items, loc="upper right", fontsize=8, ncol=2)

        self._save(fig, "genesis_locations")

    def plot_intensity_by_category(self, loader: DataLoader):
        """各分類的風速箱型圖"""
        data = []
        for rec in loader.records:
            if rec.max_sustained_wind_ms is not None:
                data.append(
                    {
                        "category": rec.taiwan_track_category,
                        "wind_ms": rec.max_sustained_wind_ms,
                    }
                )
        df = pd.DataFrame(data)
        if df.empty:
            return

        fig, ax = plt.subplots(figsize=(10, 5))
        cats = sorted(df["category"].unique())
        positions = range(len(cats))
        bp_data = [df[df["category"] == c]["wind_ms"].values for c in cats]

        bp = ax.boxplot(bp_data, positions=positions, patch_artist=True, widths=0.6)
        for patch, cat in zip(bp["boxes"], cats):
            patch.set_facecolor(_get_color(cat))
            patch.set_alpha(0.7)

        ax.set_xticks(positions)
        ax.set_xticklabels([f"類型 {c}" for c in cats], rotation=45)
        ax.set_ylabel("近中心最大風速 (m/s)")
        ax.set_title("各路徑分類的颱風強度分布")
        ax.grid(True, alpha=0.3, axis="y")
        fig.tight_layout()
        self._save(fig, "intensity_by_category")

    # ================================================================
    # 2. 特徵分析圖
    # ================================================================

    def plot_feature_heatmap(
        self, features: dict[str, TyphoonFeatures], loader: DataLoader
    ):
        """特徵相關性熱力圖"""
        ids = list(features.keys())
        vectors = np.array([features[tid].to_feature_vector() for tid in ids])
        names = TyphoonFeatures.feature_names()
        df = pd.DataFrame(vectors, columns=names)

        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(
            df.corr(),
            annot=True,
            fmt=".2f",
            cmap="RdBu_r",
            center=0,
            ax=ax,
            square=True,
            linewidths=0.5,
        )
        ax.set_title("特徵相關性熱力圖")
        fig.tight_layout()
        self._save(fig, "feature_correlation")

    def plot_feature_scatter(
        self, features: dict[str, TyphoonFeatures], loader: DataLoader
    ):
        """關鍵特徵的 2D 散布圖"""
        ids = list(features.keys())
        cats = [loader.get(tid).taiwan_track_category for tid in ids]

        feat_pairs = [
            ("min_distance_to_taiwan", "max_wind_kt"),
            ("mean_angle", "approach_speed_kmh"),
            ("birth_lon", "birth_lat"),
            ("rain_proxy", "min_pressure_mb"),
        ]

        fig, axes = plt.subplots(2, 2, figsize=(14, 12))
        axes = axes.flatten()

        for idx, (fx, fy) in enumerate(feat_pairs):
            ax = axes[idx]
            fi_x = TyphoonFeatures.feature_names().index(fx)
            fi_y = TyphoonFeatures.feature_names().index(fy)

            for tid, cat in zip(ids, cats):
                vec = features[tid].to_feature_vector()
                ax.scatter(
                    vec[fi_x],
                    vec[fi_y],
                    c=_get_color(cat),
                    s=25,
                    alpha=0.6,
                    edgecolors="none",
                )

            ax.set_xlabel(fx)
            ax.set_ylabel(fy)
            ax.set_title(f"{fx} vs {fy}")
            ax.grid(True, alpha=0.3)

        fig.suptitle("特徵散布圖（依路徑分類著色）", fontsize=14)
        fig.tight_layout()
        self._save(fig, "feature_scatter")

    # ================================================================
    # 3. 預測結果圖
    # ================================================================

    def plot_confusion_matrix(self, confusion_data: dict, categories: list[str]):
        """混淆矩陣"""
        cats = sorted(categories)
        n = len(cats)
        matrix = np.zeros((n, n), dtype=int)
        cat_to_idx = {c: i for i, c in enumerate(cats)}

        for (true, pred), count in confusion_data.items():
            if true in cat_to_idx and pred in cat_to_idx:
                matrix[cat_to_idx[true], cat_to_idx[pred]] = count

        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(
            matrix,
            annot=True,
            fmt="d",
            cmap="Blues",
            xticklabels=[f"預測 {c}" for c in cats],
            yticklabels=[f"真實 {c}" for c in cats],
            ax=ax,
            linewidths=0.5,
        )
        ax.set_title("侵臺路徑分類預測混淆矩陣")
        ax.set_xlabel("預測分類")
        ax.set_ylabel("真實分類")
        fig.tight_layout()
        self._save(fig, "confusion_matrix")

    def plot_per_category_accuracy(self, per_category: dict):
        """各分類準確率長條圖"""
        cats = sorted(per_category.keys())
        accuracies = [per_category[c]["accuracy"] for c in cats]
        counts = [per_category[c]["total"] for c in cats]

        fig, ax1 = plt.subplots(figsize=(10, 5))

        bars = ax1.bar(
            range(len(cats)), accuracies, color=[_get_color(c) for c in cats], alpha=0.8
        )
        ax1.set_xticks(range(len(cats)))
        ax1.set_xticklabels([f"類型 {c}" for c in cats])
        ax1.set_ylabel("準確率")
        ax1.set_ylim(0, 1.05)
        ax1.set_title("各路徑分類預測準確率")

        # 標注數值
        for bar, acc, cnt in zip(bars, accuracies, counts):
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                acc + 0.02,
                f"{acc:.0%}\n(n={cnt})",
                ha="center",
                va="bottom",
                fontsize=8,
            )

        ax1.grid(True, alpha=0.3, axis="y")
        fig.tight_layout()
        self._save(fig, "per_category_accuracy")

    def plot_prediction_example(
        self,
        query_rec: TyphoonRecord,
        similar_recs: list[TyphoonRecord],
        predicted_cat: str,
        confidence: float,
    ):
        """單一預測結果視覺化（查詢颱風 + 相似颱風）"""
        fig, ax = plt.subplots(figsize=(12, 10))

        # 畫相似颱風
        for i, rec in enumerate(similar_recs):
            lats = rec.track["latitude"].values
            lons = rec.track["longitude"].values
            color = _get_color(rec.taiwan_track_category)
            ax.plot(
                lons,
                lats,
                color=color,
                alpha=0.5,
                linewidth=1.5,
                label=f"{rec.name_zh} ({rec.year}) 類型{rec.taiwan_track_category}",
            )

        # 畫查詢颱風（加粗）
        q_lats = query_rec.track["latitude"].values
        q_lons = query_rec.track["longitude"].values
        ax.plot(
            q_lons,
            q_lats,
            "k-",
            linewidth=3,
            alpha=0.9,
            label=f"查詢: {query_rec.name_zh} ({query_rec.year})",
        )
        ax.plot(q_lons[0], q_lats[0], "ko", markersize=8)
        ax.plot(q_lons[-1], q_lats[-1], "ks", markersize=8)

        # 台灣
        ax.plot(TAIWAN_LON, TAIWAN_LAT, "r*", markersize=15, zorder=10)

        # 500km 圈
        theta = np.linspace(0, 2 * np.pi, 100)
        r_deg = 500 / 111
        ax.plot(
            TAIWAN_LON + r_deg * np.cos(theta),
            TAIWAN_LAT + r_deg * np.sin(theta),
            "r--",
            alpha=0.4,
            linewidth=1,
        )

        ax.set_xlabel("經度 (°E)")
        ax.set_ylabel("緯度 (°N)")
        true_cat = query_rec.taiwan_track_category
        title = (
            f"預測結果：{query_rec.name_zh} ({query_rec.year})\n"
            f"真實類型={true_cat}, 預測類型={predicted_cat}, 信心度={confidence:.1%}"
        )
        ax.set_title(title)
        ax.legend(loc="upper right", fontsize=8)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        safe_name = query_rec.typhoon_id
        cat_label = query_rec.taiwan_track_category
        self._save(fig, f"prediction_cat{cat_label}_{safe_name}")

    # ================================================================
    # 4. 批量生成
    # ================================================================

    def generate_all_analysis_plots(
        self, loader: DataLoader, features: dict[str, TyphoonFeatures]
    ):
        """一鍵生成所有分析圖"""
        print("\n📊 生成分析圖表...")
        self.plot_category_distribution(loader)
        self.plot_all_tracks(loader)
        self.plot_tracks_by_category(loader)
        self.plot_genesis_locations(loader)
        self.plot_intensity_by_category(loader)
        self.plot_feature_heatmap(features, loader)
        self.plot_feature_scatter(features, loader)
        print("✓ 所有分析圖表已生成")

    def generate_all_prediction_plots(
        self,
        eval_result: dict,
        loader: DataLoader,
        fixed_example_ids: dict[str, str] | None = None,
    ):
        """
        一鍵生成所有預測結果圖

        Args:
            eval_result: evaluate() 的結果
            loader: DataLoader
            fixed_example_ids: {category: typhoon_id} 固定範例，確保每次跑相同颱風
        """
        print("\n📊 生成預測結果圖表...")

        categories = sorted(set(r.taiwan_track_category for r in loader.records))

        self.plot_confusion_matrix(eval_result["confusion_data"], categories)
        self.plot_per_category_accuracy(eval_result["per_category"])

        # 建立預測結果索引
        pred_by_id = {r.typhoon_id: r for r in eval_result["predictions"]}

        if fixed_example_ids:
            # 使用固定範例
            for cat, tid in sorted(fixed_example_ids.items()):
                result = pred_by_id.get(tid)
                if result is None:
                    continue
                query_rec = loader.get(result.typhoon_id)
                similar_recs = []
                for st in result.similar_typhoons[:5]:
                    try:
                        similar_recs.append(loader.get(st["typhoon_id"]))
                    except KeyError:
                        pass
                if similar_recs:
                    self.plot_prediction_example(
                        query_rec,
                        similar_recs,
                        result.predicted_category,
                        result.confidence,
                    )
        else:
            # 每個分類取第一個
            shown_cats = set()
            for result in eval_result["predictions"]:
                cat = result.true_category
                if cat in shown_cats:
                    continue
                shown_cats.add(cat)

                query_rec = loader.get(result.typhoon_id)
                similar_recs = []
                for st in result.similar_typhoons[:5]:
                    try:
                        similar_recs.append(loader.get(st["typhoon_id"]))
                    except KeyError:
                        pass

                if similar_recs:
                    self.plot_prediction_example(
                        query_rec,
                        similar_recs,
                        result.predicted_category,
                        result.confidence,
                    )

        print("✓ 所有預測結果圖表已生成")

    # ================================================================
    # 5. 降水 EDA 分析圖
    # ================================================================

    def plot_rainfall_distribution(self, rainfall_records: dict, loader: DataLoader):
        """各地區降水量分布直方圖（rainfall_records: {tid: {region: mm}}）"""
        codes = region_codes()
        fig, axes = plt.subplots(1, len(codes), figsize=(7 * len(codes), 5), squeeze=False)

        for idx, code in enumerate(codes):
            ax = axes[0][idx]
            station = region_label(code)
            vals = []
            for tid, rec in rainfall_records.items():
                v = rec.get(code) if isinstance(rec, dict) else None
                if v is not None:
                    vals.append(v)
            if vals:
                ax.hist(vals, bins=30, color="#377eb8", alpha=0.7, edgecolor="white")
                ax.axvline(
                    np.mean(vals),
                    color="red",
                    linestyle="--",
                    label=f"平均 {np.mean(vals):.0f} mm",
                )
                ax.axvline(
                    np.median(vals),
                    color="orange",
                    linestyle="--",
                    label=f"中位數 {np.median(vals):.0f} mm",
                )
                ax.set_xlabel("事件降水量 (mm)")
                ax.set_ylabel("颱風數")
                ax.set_title(f"{station} — 颱風事件降水量分布 (n={len(vals)})")
                ax.legend(fontsize=8)
                ax.grid(True, alpha=0.3, axis="y")

        fig.tight_layout()
        self._save(fig, "rainfall_distribution")

    def plot_rainfall_by_category(self, rainfall_records: dict, loader: DataLoader):
        """各路徑分類的降水箱型圖（各地區）"""
        codes = region_codes()
        fig, axes = plt.subplots(1, len(codes), figsize=(7 * len(codes), 6), squeeze=False)

        for idx, code in enumerate(codes):
            ax = axes[0][idx]
            station = region_label(code)
            cat_data = {}
            for rec in loader.records:
                rr = rainfall_records.get(rec.typhoon_id)
                if rr is None:
                    continue
                v = rr.get(code) if isinstance(rr, dict) else None
                if v is not None:
                    cat_data.setdefault(rec.taiwan_track_category, []).append(v)

            cats = sorted(cat_data.keys())
            if cats:
                bp = ax.boxplot(
                    [cat_data[c] for c in cats],
                    labels=[f"類型{c}" for c in cats],
                    patch_artist=True,
                )
                for patch, cat in zip(bp["boxes"], cats):
                    patch.set_facecolor(_get_color(cat))
                    patch.set_alpha(0.6)
                ax.set_ylabel("事件降水量 (mm)")
                ax.set_title(f"{station} — 各路徑分類降水量分布")
                ax.grid(True, alpha=0.3, axis="y")
                ax.tick_params(axis="x", rotation=45)

        fig.tight_layout()
        self._save(fig, "rainfall_by_category")

    def plot_rainfall_correlation(
        self, rainfall_records: dict, loader: DataLoader, features: dict
    ):
        """降水量與特徵的相關性熱力圖"""
        rows = []
        for tid, feat in features.items():
            rr = rainfall_records.get(tid)
            if rr is None:
                continue
            vec = feat.to_feature_vector()
            names = TyphoonFeatures.feature_names()
            row = {name: vec[i] for i, name in enumerate(names)}
            has_any = False
            for code in region_codes():
                v = rr.get(code) if isinstance(rr, dict) else None
                if v is not None:
                    row[f"rainfall_{code}"] = v
                    has_any = True
            if has_any:
                rows.append(row)

        if not rows:
            return

        df = pd.DataFrame(rows).dropna()
        if len(df) < 5:
            return

        # 只看降水與其他特徵的相關性
        corr = df.corr()
        rainfall_cols = [
            f"rainfall_{code}" for code in region_codes()
            if f"rainfall_{code}" in corr.columns
        ]
        if not rainfall_cols:
            return

        feature_cols = [c for c in corr.columns if c not in rainfall_cols]
        sub_corr = corr.loc[feature_cols, rainfall_cols]

        fig, ax = plt.subplots(figsize=(6, 8))
        sns.heatmap(
            sub_corr,
            annot=True,
            fmt=".2f",
            cmap="RdBu_r",
            center=0,
            ax=ax,
            linewidths=0.5,
            vmin=-1,
            vmax=1,
        )
        ax.set_title("降水量與颱風特徵相關性")
        ax.set_xticklabels(
            [f"{region_label(c)}降水" for c in region_codes() if f"rainfall_{c}" in rainfall_cols],
            rotation=0,
        )
        fig.tight_layout()
        self._save(fig, "rainfall_feature_correlation")

    def plot_rainfall_scatter_by_feature(
        self, rainfall_records: dict, loader: DataLoader, features: dict
    ):
        """降水量與關鍵特徵散布圖"""
        key_features = [
            "min_distance_to_taiwan",
            "max_wind_kt",
            "mean_angle",
            "duration_hours",
        ]
        available = TyphoonFeatures.feature_names()

        fig, axes = plt.subplots(2, 2, figsize=(14, 12))
        axes = axes.flatten()

        for idx, fname in enumerate(key_features):
            ax = axes[idx]
            if fname not in available:
                ax.set_visible(False)
                continue
            fi = available.index(fname)

            x_vals, y_vals, colors = [], [], []
            for tid, feat in features.items():
                rr = rainfall_records.get(tid)
                if rr is None:
                    continue
                # 用各地區平均
                vals = [
                    rr.get(code) for code in region_codes()
                    if isinstance(rr, dict) and rr.get(code) is not None
                ]
                if not vals:
                    continue
                vec = feat.to_feature_vector()
                x_vals.append(vec[fi])
                y_vals.append(np.mean(vals))
                colors.append(_get_color(loader.get(tid).taiwan_track_category))

            if x_vals:
                ax.scatter(x_vals, y_vals, c=colors, alpha=0.5, s=25, edgecolors="none")
                ax.set_xlabel(fname)
                ax.set_ylabel("平均降水量 (mm)")
                ax.set_title(f"降水量 vs {fname}")
                ax.grid(True, alpha=0.3)

        fig.suptitle("降水量與關鍵特徵關係（依路徑分類著色）", fontsize=13)
        fig.tight_layout()
        self._save(fig, "rainfall_vs_features")

    def generate_all_rainfall_eda_plots(
        self, rainfall_records: dict, loader: DataLoader, features: dict
    ):
        """一鍵生成所有降水 EDA 分析圖"""
        print("\n📊 生成降水 EDA 分析圖表...")
        self.plot_rainfall_distribution(rainfall_records, loader)
        self.plot_rainfall_by_category(rainfall_records, loader)
        self.plot_rainfall_correlation(rainfall_records, loader, features)
        self.plot_rainfall_scatter_by_feature(rainfall_records, loader, features)
        print("✓ 所有降水 EDA 圖表已生成")
