"""
颱風事件降水分析模組（region-based）

功能：
  1. 從統一資料源（typhoons_overview.json，經 DataLoader）讀取各地區事件降水
  2. 計算預測颱風與類比颱風的降水損失
  3. 生成降水機率分布
  4. 提供降水統計分析

地區由 stage00 的 RAINFALL_REGIONS 集中定義（tn=臺南、kh=高雄…可擴充）。
不再依賴 CSV，資料統一來自 overview 的 event_rain_* 欄位。
"""

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from data_pipiline.stage00_data_ingestion.typhoon.loader import DataLoader
from data_pipiline.stage00_data_ingestion.typhoon.regions import (
    RAINFALL_REGIONS,
    region_codes,
    region_label,
)


# 嘗試設定中文字型
def _setup_chinese_font():
    candidates = [
        "Microsoft JhengHei",
        "Microsoft YaHei",
        "SimHei",
        "PingFang TC",
        "Noto Sans CJK TC",
    ]
    for font_name in candidates:
        try:
            fm.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


_setup_chinese_font()


@dataclass
class RainfallAnalysisResult:
    """單一預測的降水分析結果（dict 皆以 region code 為 key）"""

    target_id: str
    target_rainfall: dict  # {region: mm}
    analog_rainfalls: list[dict]  # [{typhoon_id, region: mm, ...}]
    loss_mae: dict  # {region: MAE}
    loss_rmse: dict  # {region: RMSE}
    probability_distribution: dict  # {region: {...}}


class RainfallAnalyzer:
    """颱風事件降水分析器（資料來源：overview event_rain_*）"""

    def __init__(
        self,
        loader: Optional[DataLoader] = None,
        processed_dir: str = "data/typhoon/preprocessed",
        regions: Optional[list[str]] = None,
    ):
        self._loader = loader
        self._processed_dir = processed_dir
        self.regions = regions or region_codes()
        # {typhoon_id: {region: mm|None}}
        self._records: dict[str, dict[str, Optional[float]]] = {}

    def load(self) -> "RainfallAnalyzer":
        loader = self._loader
        if loader is None:
            loader = DataLoader(self._processed_dir).load()
            self._loader = loader

        for rec in loader.records:
            self._records[rec.typhoon_id] = {
                code: rec.get_rainfall(code) for code in self.regions
            }

        n_with = sum(
            1 for v in self._records.values() if any(x is not None for x in v.values())
        )
        print(f"✓ 已載入 {len(self._records)} 筆降水資料（{n_with} 筆有值，地區={self.regions}）")
        return self

    def get_rainfall(self, typhoon_id: str) -> Optional[dict]:
        """取得單一颱風各地區降水 {region: mm}，無紀錄回傳 None"""
        return self._records.get(typhoon_id)

    def has_data(self, typhoon_id: str, region: Optional[str] = None) -> bool:
        rec = self._records.get(typhoon_id)
        if rec is None:
            return False
        if region is not None:
            return rec.get(region) is not None
        return any(v is not None for v in rec.values())

    def analyze_prediction(
        self,
        target_id: str,
        analog_ids: list[str],
        analog_distances: list[float] = None,
        regions: Optional[list[str]] = None,
    ) -> Optional[RainfallAnalysisResult]:
        """分析單一預測的降水結果（各地區）"""
        regions = regions or self.regions
        target_rec = self._records.get(target_id)
        if target_rec is None:
            return None

        target_rainfall = {r: target_rec.get(r) for r in regions}

        analog_rainfalls = []
        for i, aid in enumerate(analog_ids):
            arec = self._records.get(aid)
            if arec is None:
                continue
            entry = {"typhoon_id": aid}
            for r in regions:
                entry[r] = arec.get(r)
            if analog_distances and i < len(analog_distances):
                entry["distance"] = analog_distances[i]
            analog_rainfalls.append(entry)

        if not analog_rainfalls:
            return None

        loss_mae, loss_rmse, prob_dist = {}, {}, {}
        for region in regions:
            target_val = target_rainfall.get(region)
            analog_vals = [
                ar[region] for ar in analog_rainfalls if ar.get(region) is not None
            ]

            if target_val is not None and analog_vals:
                errors = [abs(target_val - av) for av in analog_vals]
                loss_mae[region] = float(np.mean(errors))
                loss_rmse[region] = float(np.sqrt(np.mean([e**2 for e in errors])))
            else:
                loss_mae[region] = None
                loss_rmse[region] = None

            if analog_vals:
                sorted_vals = sorted(analog_vals)
                percentiles = [10, 25, 50, 75, 90]
                pct_values = [float(np.percentile(sorted_vals, p)) for p in percentiles]
                prob_dist[region] = {
                    "values": sorted_vals,
                    "percentiles": dict(zip(percentiles, pct_values)),
                    "mean": float(np.mean(sorted_vals)),
                    "median": float(np.median(sorted_vals)),
                    "std": float(np.std(sorted_vals)) if len(sorted_vals) > 1 else 0.0,
                    "min": float(min(sorted_vals)),
                    "max": float(max(sorted_vals)),
                }
            else:
                prob_dist[region] = None

        return RainfallAnalysisResult(
            target_id=target_id,
            target_rainfall=target_rainfall,
            analog_rainfalls=analog_rainfalls,
            loss_mae=loss_mae,
            loss_rmse=loss_rmse,
            probability_distribution=prob_dist,
        )

    def evaluate_all(
        self,
        predictions: list[dict],
        regions: Optional[list[str]] = None,
    ) -> dict:
        """對所有預測結果進行降水分析（各地區彙整）"""
        regions = regions or self.regions
        results = []
        all_errors = {r: [] for r in regions}

        for pred in predictions:
            tid = pred["typhoon_id"]
            analog_ids = [st["typhoon_id"] for st in pred.get("similar_typhoons", [])]
            analog_dists = [
                st.get("distance", 0) for st in pred.get("similar_typhoons", [])
            ]
            analysis = self.analyze_prediction(tid, analog_ids, analog_dists, regions)
            if analysis:
                results.append(analysis)
                for region in regions:
                    if analysis.loss_mae.get(region) is not None:
                        all_errors[region].append(analysis.loss_mae[region])

        overall_mae, overall_rmse = {}, {}
        for region in regions:
            errs = all_errors[region]
            if errs:
                overall_mae[region] = float(np.mean(errs))
                overall_rmse[region] = float(np.sqrt(np.mean([e**2 for e in errs])))
            else:
                overall_mae[region] = None
                overall_rmse[region] = None

        first_region = regions[0] if regions else None
        return {
            "regions": regions,
            "overall_mae": overall_mae,
            "overall_rmse": overall_rmse,
            "count": len(results),
            "total_with_data": sum(
                1
                for r in results
                if first_region and r.loss_mae.get(first_region) is not None
            ),
            "per_prediction": results,
        }

    # ================================================================
    # 視覺化
    # ================================================================

    def generate_plots(self, eval_results: dict, output_dir: str):
        """生成降水分析圖表"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        predictions = eval_results.get("per_prediction", [])
        if not predictions:
            return
        regions = eval_results.get("regions", self.regions)

        self._plot_scatter(predictions, out, regions)
        self._plot_error_dist(predictions, out, regions)

    def _plot_scatter(self, predictions, out: Path, regions: list[str]):
        """實際降水 vs 類比降水散佈圖"""
        n = len(regions)
        fig, axes = plt.subplots(1, n, figsize=(7 * n, 6), squeeze=False)

        for idx, region in enumerate(regions):
            ax = axes[0][idx]
            actual_vals, analog_means = [], []
            for pred in predictions:
                target_val = pred.target_rainfall.get(region)
                prob = pred.probability_distribution.get(region)
                if target_val is not None and prob is not None:
                    actual_vals.append(target_val)
                    analog_means.append(prob["mean"])

            if actual_vals:
                ax.scatter(actual_vals, analog_means, alpha=0.5, s=30, color="#377eb8")
                max_val = max(max(actual_vals), max(analog_means)) * 1.1
                ax.plot([0, max_val], [0, max_val], "r--", alpha=0.5, label="完美預測")
                ax.set_xlabel("實際降水量 (mm)")
                ax.set_ylabel("類比平均降水量 (mm)")
                ax.set_title(
                    f"{region_label(region)} — 降水預測散佈圖 (n={len(actual_vals)})"
                )
                ax.legend()
                ax.grid(True, alpha=0.3)

        plt.tight_layout()
        fig.savefig(out / "rainfall_scatter.png", dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'rainfall_scatter.png'}")

    def _plot_error_dist(self, predictions, out: Path, regions: list[str]):
        """降水誤差分布直方圖"""
        n = len(regions)
        fig, axes = plt.subplots(1, n, figsize=(7 * n, 6), squeeze=False)

        for idx, region in enumerate(regions):
            ax = axes[0][idx]
            errors = []
            for pred in predictions:
                target_val = pred.target_rainfall.get(region)
                prob = pred.probability_distribution.get(region)
                if target_val is not None and prob is not None:
                    errors.append(target_val - prob["mean"])

            if errors:
                ax.hist(errors, bins=30, alpha=0.7, color="#4daf4a", edgecolor="white")
                ax.axvline(0, color="red", linestyle="--", alpha=0.7, label="零誤差")
                ax.axvline(
                    np.mean(errors), color="blue", linestyle="--", alpha=0.7,
                    label=f"平均={np.mean(errors):.1f}mm",
                )
                ax.set_xlabel("預測誤差 (mm) [實際 - 類比平均]")
                ax.set_ylabel("次數")
                ax.set_title(f"{region_label(region)} — 降水預測誤差分布 (n={len(errors)})")
                ax.legend()
                ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        fig.savefig(out / "rainfall_error_dist.png", dpi=150, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'rainfall_error_dist.png'}")

    def get_category_rainfall_stats(self, loader: DataLoader) -> dict:
        """計算各分類的降水統計（各地區）"""
        stats: dict[str, dict[str, list]] = {}
        for rec in loader.records:
            cat = rec.taiwan_track_category
            rain = self._records.get(rec.typhoon_id)
            if rain is None:
                continue
            stats.setdefault(cat, {r: [] for r in self.regions})
            for region in self.regions:
                v = rain.get(region)
                if v is not None:
                    stats[cat][region].append(v)

        result = {}
        for cat, data in stats.items():
            result[cat] = {}
            for region in self.regions:
                vals = data.get(region, [])
                if vals:
                    result[cat][region] = {
                        "mean": round(float(np.mean(vals)), 1),
                        "median": round(float(np.median(vals)), 1),
                        "std": round(float(np.std(vals)), 1),
                        "max": round(float(max(vals)), 1),
                        "min": round(float(min(vals)), 1),
                        "count": len(vals),
                    }
        return result

    def generate_category_rainfall_plot(self, loader: DataLoader, output_dir: str):
        """生成各分類的降水統計圖（各地區）"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        valid_cats = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
        n = len(self.regions)
        fig, axes = plt.subplots(1, n, figsize=(8 * n, 6), squeeze=False)
        colors = [
            "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00",
            "#a65628", "#f781bf", "#999999", "#66c2a5",
        ]

        for idx, region in enumerate(self.regions):
            ax = axes[0][idx]
            cat_data = {c: [] for c in valid_cats}
            for rec in loader.records:
                if rec.taiwan_track_category not in valid_cats:
                    continue
                rain = self._records.get(rec.typhoon_id)
                if rain and rain.get(region) is not None:
                    cat_data[rec.taiwan_track_category].append(rain[region])

            bp = ax.boxplot(
                [cat_data[c] for c in valid_cats],
                patch_artist=True,
            )
            # 跨 matplotlib 版本相容（3.9 起 boxplot 的 labels 改名為 tick_labels）
            ax.set_xticklabels([f"Cat {c}" for c in valid_cats])
            for patch, color in zip(bp["boxes"], colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.5)
            ax.set_xlabel("路徑分類")
            ax.set_ylabel("事件雨量 (mm)")
            ax.set_title(f"{region_label(region)} — 各分類事件雨量分布")
            ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        fig.savefig(
            out / "category_rainfall_boxplot.png", dpi=150, bbox_inches="tight", facecolor="white"
        )
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'category_rainfall_boxplot.png'}")
