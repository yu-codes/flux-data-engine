"""
颱風事件降水分析模組

功能：
  1. 載入颱風事件降水資料 (CSV)
  2. 計算預測颱風與類比颱風的降水損失
  3. 生成降水機率分布
  4. 提供降水統計分析

資料來源：data/typhoon/raw/typhoon_events_rainfall/颱風事件雨量.csv
"""

import csv
import json
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


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


RAINFALL_STATIONS = ["臺南", "高雄"]


@dataclass
class RainfallRecord:
    """單一颱風的降水資料"""

    typhoon_id: str
    tainan_mm: Optional[float]
    kaohsiung_mm: Optional[float]

    @property
    def total_mm(self) -> Optional[float]:
        vals = [v for v in [self.tainan_mm, self.kaohsiung_mm] if v is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass
class RainfallAnalysisResult:
    """降水分析結果"""

    target_id: str
    target_rainfall: dict  # {station: mm}
    analog_rainfalls: list[dict]  # [{typhoon_id, station: mm, ...}]
    loss_mae: dict  # {station: MAE}
    loss_rmse: dict  # {station: RMSE}
    probability_distribution: dict  # {station: {percentiles, values}}


class RainfallAnalyzer:
    """颱風事件降水分析器"""

    def __init__(
        self, rainfall_csv: str = "data/typhoon/raw/typhoon_events_rainfall/颱風事件雨量.csv"
    ):
        self._csv_path = Path(rainfall_csv)
        self._records: dict[str, RainfallRecord] = {}

    def load(self) -> "RainfallAnalyzer":
        if not self._csv_path.exists():
            print(f"⚠ 降水資料不存在: {self._csv_path}")
            return self

        with open(self._csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tid = row["颱風編號"].strip()
                tainan = self._parse_float(row.get("事件雨量-臺南", ""))
                kaohsiung = self._parse_float(row.get("事件雨量-高雄", ""))
                self._records[tid] = RainfallRecord(
                    typhoon_id=tid,
                    tainan_mm=tainan,
                    kaohsiung_mm=kaohsiung,
                )

        print(f"✓ 已載入 {len(self._records)} 筆降水資料")
        return self

    def _parse_float(self, val: str) -> Optional[float]:
        if not val or val.strip() == "":
            return None
        try:
            return float(val.strip())
        except (ValueError, TypeError):
            return None

    def get_rainfall(self, typhoon_id: str) -> Optional[RainfallRecord]:
        return self._records.get(typhoon_id)

    def has_data(self, typhoon_id: str) -> bool:
        rec = self._records.get(typhoon_id)
        return rec is not None and rec.total_mm is not None

    def analyze_prediction(
        self,
        target_id: str,
        analog_ids: list[str],
        analog_distances: list[float] = None,
    ) -> Optional[RainfallAnalysisResult]:
        """
        分析單一預測的降水結果

        Args:
            target_id: 目標颱風 ID
            analog_ids: k 個類比颱風的 ID
            analog_distances: 類比颱風的距離（用於加權）

        Returns:
            RainfallAnalysisResult 或 None（無降水資料時）
        """
        target_rec = self._records.get(target_id)
        if target_rec is None:
            return None

        target_rainfall = {
            "臺南": target_rec.tainan_mm,
            "高雄": target_rec.kaohsiung_mm,
        }

        analog_rainfalls = []
        for i, aid in enumerate(analog_ids):
            arec = self._records.get(aid)
            if arec is None:
                continue
            entry = {
                "typhoon_id": aid,
                "臺南": arec.tainan_mm,
                "高雄": arec.kaohsiung_mm,
            }
            if analog_distances and i < len(analog_distances):
                entry["distance"] = analog_distances[i]
            analog_rainfalls.append(entry)

        if not analog_rainfalls:
            return None

        # 計算損失 (MAE, RMSE)
        loss_mae = {}
        loss_rmse = {}
        prob_dist = {}

        for station in RAINFALL_STATIONS:
            target_val = target_rainfall.get(station)
            analog_vals = [
                ar[station] for ar in analog_rainfalls if ar.get(station) is not None
            ]

            if target_val is not None and analog_vals:
                errors = [abs(target_val - av) for av in analog_vals]
                loss_mae[station] = float(np.mean(errors))
                loss_rmse[station] = float(np.sqrt(np.mean([e**2 for e in errors])))
            else:
                loss_mae[station] = None
                loss_rmse[station] = None

            # 機率分布
            if analog_vals:
                sorted_vals = sorted(analog_vals)
                percentiles = [10, 25, 50, 75, 90]
                pct_values = [float(np.percentile(sorted_vals, p)) for p in percentiles]
                prob_dist[station] = {
                    "values": sorted_vals,
                    "percentiles": dict(zip(percentiles, pct_values)),
                    "mean": float(np.mean(sorted_vals)),
                    "std": float(np.std(sorted_vals)) if len(sorted_vals) > 1 else 0.0,
                    "min": float(min(sorted_vals)),
                    "max": float(max(sorted_vals)),
                }
            else:
                prob_dist[station] = None

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
    ) -> dict:
        """
        對所有預測結果進行降水分析

        Args:
            predictions: list of {typhoon_id, similar_typhoons: [{typhoon_id, distance}, ...]}

        Returns:
            {
                overall_mae: {station: float},
                overall_rmse: {station: float},
                per_prediction: [RainfallAnalysisResult, ...],
                category_stats: {cat: {station: {mean_mae, mean_rmse}}},
            }
        """
        results = []
        all_errors = {s: [] for s in RAINFALL_STATIONS}

        for pred in predictions:
            tid = pred["typhoon_id"]
            analog_ids = [st["typhoon_id"] for st in pred.get("similar_typhoons", [])]
            analog_dists = [
                st.get("distance", 0) for st in pred.get("similar_typhoons", [])
            ]
            analysis = self.analyze_prediction(tid, analog_ids, analog_dists)
            if analysis:
                results.append(analysis)
                for station in RAINFALL_STATIONS:
                    if analysis.loss_mae.get(station) is not None:
                        all_errors[station].append(analysis.loss_mae[station])

        overall_mae = {}
        overall_rmse = {}
        for station in RAINFALL_STATIONS:
            errs = all_errors[station]
            if errs:
                overall_mae[station] = float(np.mean(errs))
                overall_rmse[station] = float(np.sqrt(np.mean([e**2 for e in errs])))
            else:
                overall_mae[station] = None
                overall_rmse[station] = None

        return {
            "overall_mae": overall_mae,
            "overall_rmse": overall_rmse,
            "count": len(results),
            "total_with_data": sum(
                1 for r in results if r.loss_mae.get("臺南") is not None
            ),
            "per_prediction": results,
        }

    def generate_plots(self, eval_results: dict, output_dir: str):
        """生成降水分析圖表"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        predictions = eval_results.get("per_prediction", [])
        if not predictions:
            return

        # 1. 降水預測散佈圖（實際 vs 類比平均）
        self._plot_scatter(predictions, out)

        # 2. 各站降水機率分布箱型圖
        self._plot_box(predictions, out)

        # 3. 降水誤差分布
        self._plot_error_dist(predictions, out)

    def _plot_scatter(self, predictions: list[RainfallAnalysisResult], out: Path):
        """實際降水 vs 類比降水散佈圖"""
        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        for idx, station in enumerate(RAINFALL_STATIONS):
            ax = axes[idx]
            actual_vals = []
            analog_means = []

            for pred in predictions:
                target_val = pred.target_rainfall.get(station)
                prob = pred.probability_distribution.get(station)
                if target_val is not None and prob is not None:
                    actual_vals.append(target_val)
                    analog_means.append(prob["mean"])

            if actual_vals:
                ax.scatter(actual_vals, analog_means, alpha=0.5, s=30, color="#377eb8")
                max_val = max(max(actual_vals), max(analog_means)) * 1.1
                ax.plot([0, max_val], [0, max_val], "r--", alpha=0.5, label="完美預測")
                ax.set_xlabel("實際降水量 (mm)")
                ax.set_ylabel("類比平均降水量 (mm)")
                ax.set_title(f"{station} — 降水預測散佈圖 (n={len(actual_vals)})")
                ax.legend()
                ax.grid(True, alpha=0.3)

        plt.tight_layout()
        fig.savefig(
            out / "rainfall_scatter.png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'rainfall_scatter.png'}")

    def _plot_box(self, predictions: list[RainfallAnalysisResult], out: Path):
        """降水機率分布箱型圖"""
        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        for idx, station in enumerate(RAINFALL_STATIONS):
            ax = axes[idx]
            data_groups = []
            labels = []
            actuals = []

            # 取前 20 個有資料的預測來顯示
            count = 0
            for pred in predictions:
                prob = pred.probability_distribution.get(station)
                target_val = pred.target_rainfall.get(station)
                if prob is not None and prob["values"] and target_val is not None:
                    data_groups.append(prob["values"])
                    labels.append(pred.target_id[-4:])
                    actuals.append(target_val)
                    count += 1
                    if count >= 20:
                        break

            if data_groups:
                bp = ax.boxplot(data_groups, labels=labels, patch_artist=True)
                for patch in bp["boxes"]:
                    patch.set_facecolor("#377eb8")
                    patch.set_alpha(0.5)
                ax.scatter(
                    range(1, len(actuals) + 1),
                    actuals,
                    color="red",
                    zorder=5,
                    s=30,
                    label="實際值",
                    marker="D",
                )
                ax.set_xlabel("颱風")
                ax.set_ylabel("降水量 (mm)")
                ax.set_title(f"{station} — 類比降水機率分布")
                ax.legend()
                ax.tick_params(axis="x", rotation=45)
                ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        fig.savefig(
            out / "rainfall_probability.png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'rainfall_probability.png'}")

    def _plot_error_dist(self, predictions: list[RainfallAnalysisResult], out: Path):
        """降水誤差分布直方圖"""
        fig, axes = plt.subplots(1, 2, figsize=(14, 6))

        for idx, station in enumerate(RAINFALL_STATIONS):
            ax = axes[idx]
            errors = []
            for pred in predictions:
                target_val = pred.target_rainfall.get(station)
                prob = pred.probability_distribution.get(station)
                if target_val is not None and prob is not None:
                    errors.append(target_val - prob["mean"])

            if errors:
                ax.hist(errors, bins=30, alpha=0.7, color="#4daf4a", edgecolor="white")
                ax.axvline(0, color="red", linestyle="--", alpha=0.7, label="零誤差")
                ax.axvline(
                    np.mean(errors),
                    color="blue",
                    linestyle="--",
                    alpha=0.7,
                    label=f"平均={np.mean(errors):.1f}mm",
                )
                ax.set_xlabel("預測誤差 (mm) [實際 - 類比平均]")
                ax.set_ylabel("次數")
                ax.set_title(f"{station} — 降水預測誤差分布 (n={len(errors)})")
                ax.legend()
                ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        fig.savefig(
            out / "rainfall_error_dist.png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'rainfall_error_dist.png'}")

    def get_category_rainfall_stats(self, loader) -> dict:
        """計算各分類的降水統計"""
        stats = {}
        for rec in loader.records:
            cat = rec.taiwan_track_category
            rain = self._records.get(rec.typhoon_id)
            if rain is None:
                continue
            if cat not in stats:
                stats[cat] = {"臺南": [], "高雄": []}
            if rain.tainan_mm is not None:
                stats[cat]["臺南"].append(rain.tainan_mm)
            if rain.kaohsiung_mm is not None:
                stats[cat]["高雄"].append(rain.kaohsiung_mm)

        result = {}
        for cat, data in stats.items():
            result[cat] = {}
            for station in RAINFALL_STATIONS:
                vals = data.get(station, [])
                if vals:
                    result[cat][station] = {
                        "mean": round(float(np.mean(vals)), 1),
                        "median": round(float(np.median(vals)), 1),
                        "std": round(float(np.std(vals)), 1),
                        "max": round(float(max(vals)), 1),
                        "min": round(float(min(vals)), 1),
                        "count": len(vals),
                    }
        return result

    def generate_category_rainfall_plot(self, loader, output_dir: str):
        """生成各分類的降水統計圖"""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        stats = self.get_category_rainfall_stats(loader)
        if not stats:
            return

        fig, axes = plt.subplots(1, 2, figsize=(16, 6))

        for idx, station in enumerate(RAINFALL_STATIONS):
            ax = axes[idx]
            cats = sorted(
                [
                    c
                    for c in stats.keys()
                    if c in ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
                ]
            )
            data_groups = []
            for cat in cats:
                if station in stats.get(cat, {}):
                    vals = []
                    for rec in loader.records:
                        if rec.taiwan_track_category == cat:
                            rain = self._records.get(rec.typhoon_id)
                            if rain:
                                v = (
                                    rain.tainan_mm
                                    if station == "臺南"
                                    else rain.kaohsiung_mm
                                )
                                if v is not None:
                                    vals.append(v)
                    data_groups.append(vals)
                else:
                    data_groups.append([])

            bp = ax.boxplot(
                data_groups, labels=[f"Cat {c}" for c in cats], patch_artist=True
            )
            colors = [
                "#e41a1c",
                "#377eb8",
                "#4daf4a",
                "#984ea3",
                "#ff7f00",
                "#a65628",
                "#f781bf",
                "#999999",
                "#66c2a5",
            ]
            for patch, color in zip(bp["boxes"], colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.5)

            ax.set_xlabel("路徑分類")
            ax.set_ylabel("事件雨量 (mm)")
            ax.set_title(f"{station} — 各分類事件雨量分布")
            ax.grid(True, alpha=0.3, axis="y")

        plt.tight_layout()
        fig.savefig(
            out / "category_rainfall_boxplot.png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
        )
        plt.close(fig)
        print(f"  ✓ 已儲存：{out / 'category_rainfall_boxplot.png'}")
