"""
颱風資料探索性分析模組 (EDA)

提供各種統計摘要和分析功能，讓使用者在預測之前先了解資料分布
"""

import numpy as np
import pandas as pd
from collections import Counter
from typing import Any

from data_pipiline.stage00_data_ingestion.typhoon.loader import DataLoader
from data_pipiline.stage04_feature_engineering.typhoon.extractor import (
    TyphoonFeatureExtractor,
    TyphoonFeatures,
    haversine_vec,
    TAIWAN_LAT,
    TAIWAN_LON,
)


class TyphoonEDA:
    """颱風資料探索性分析"""

    def __init__(self, loader: DataLoader):
        self.loader = loader
        self.overview_df = loader.to_overview_dataframe()

    def category_distribution(self) -> pd.DataFrame:
        """路徑分類分布統計"""
        counts = self.overview_df["taiwan_track_category"].value_counts()
        df = counts.reset_index()
        df.columns = ["category", "count"]
        df["percentage"] = (df["count"] / df["count"].sum() * 100).round(1)
        return df.sort_values("category")

    def yearly_distribution(self) -> pd.DataFrame:
        """按年份統計颱風數"""
        counts = self.overview_df["year"].value_counts().sort_index()
        df = counts.reset_index()
        df.columns = ["year", "count"]
        return df

    def intensity_stats(self) -> pd.DataFrame:
        """各路徑分類的強度統計"""
        rows = []
        for cat in sorted(self.overview_df["taiwan_track_category"].unique()):
            subset = self.overview_df[self.overview_df["taiwan_track_category"] == cat]
            wind = subset["max_sustained_wind_ms"].dropna()
            pressure = subset["min_pressure"].dropna()
            rows.append(
                {
                    "category": cat,
                    "count": len(subset),
                    "avg_wind_ms": round(wind.mean(), 1) if len(wind) > 0 else None,
                    "max_wind_ms": round(wind.max(), 1) if len(wind) > 0 else None,
                    "avg_pressure_mb": (
                        round(pressure.mean(), 1) if len(pressure) > 0 else None
                    ),
                    "min_pressure_mb": (
                        round(pressure.min(), 1) if len(pressure) > 0 else None
                    ),
                }
            )
        return pd.DataFrame(rows)

    def genesis_location_stats(self) -> pd.DataFrame:
        """各路徑分類的生成位置統計"""
        rows = []
        for cat in sorted(self.overview_df["taiwan_track_category"].unique()):
            subset = self.overview_df[self.overview_df["taiwan_track_category"] == cat]
            lon = subset["birth_lon"].dropna()
            lat = subset["birth_lat"].dropna()
            rows.append(
                {
                    "category": cat,
                    "count": len(subset),
                    "avg_lon": round(lon.mean(), 1) if len(lon) > 0 else None,
                    "avg_lat": round(lat.mean(), 1) if len(lat) > 0 else None,
                    "lon_std": round(lon.std(), 2) if len(lon) > 1 else None,
                    "lat_std": round(lat.std(), 2) if len(lat) > 1 else None,
                }
            )
        return pd.DataFrame(rows)

    def track_length_stats(self) -> pd.DataFrame:
        """各路徑分類的軌跡長度統計"""
        rows = []
        for cat in sorted(self.overview_df["taiwan_track_category"].unique()):
            subset = self.overview_df[self.overview_df["taiwan_track_category"] == cat]
            pts = subset["track_point_count"]
            rows.append(
                {
                    "category": cat,
                    "count": len(subset),
                    "avg_track_points": round(pts.mean(), 1),
                    "min_track_points": int(pts.min()),
                    "max_track_points": int(pts.max()),
                }
            )
        return pd.DataFrame(rows)

    def compute_all_min_distances(self) -> pd.DataFrame:
        """計算所有颱風距台灣最近距離"""
        rows = []
        for rec in self.loader.records:
            lats = rec.track["latitude"].values.astype(float)
            lons = rec.track["longitude"].values.astype(float)
            dists = haversine_vec(lats, lons)
            min_dist = float(np.min(dists))
            rows.append(
                {
                    "typhoon_id": rec.typhoon_id,
                    "name_zh": rec.name_zh,
                    "category": rec.taiwan_track_category,
                    "min_distance_km": round(min_dist, 1),
                }
            )
        return pd.DataFrame(rows)

    def feature_correlation(self, features: dict[str, TyphoonFeatures]) -> pd.DataFrame:
        """特徵相關性矩陣"""
        ids = list(features.keys())
        vectors = np.array([features[tid].to_feature_vector() for tid in ids])
        names = TyphoonFeatures.feature_names()
        df = pd.DataFrame(vectors, columns=names)
        return df.corr()

    def full_report(
        self, features: dict[str, TyphoonFeatures] = None
    ) -> dict[str, Any]:
        """生成完整的 EDA 報告"""
        report = {
            "total_typhoons": len(self.loader.records),
            "year_range": (
                min(r.year for r in self.loader.records),
                max(r.year for r in self.loader.records),
            ),
            "categories": sorted(
                set(r.taiwan_track_category for r in self.loader.records)
            ),
            "category_distribution": self.category_distribution(),
            "intensity_stats": self.intensity_stats(),
            "genesis_stats": self.genesis_location_stats(),
            "track_length_stats": self.track_length_stats(),
            "min_distances": self.compute_all_min_distances(),
        }

        if features:
            report["feature_correlation"] = self.feature_correlation(features)

        return report

    def print_summary(self):
        """印出 EDA 摘要"""
        print(f"\n📊 資料集概覽")
        print(f"  總颱風數：{len(self.loader.records)}")
        years = [r.year for r in self.loader.records]
        print(f"  年份範圍：{min(years)} ~ {max(years)}")
        print(f"\n  路徑分類分布：")
        dist = self.category_distribution()
        for _, row in dist.iterrows():
            print(
                f"    類型 {row['category']}：{row['count']} 筆 ({row['percentage']}%)"
            )
