"""
數據加載模組
職責：讀取 processed JSON，提供統一的資料存取介面
"""

import json
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TyphoonRecord:
    """單一颱風的完整資料"""

    typhoon_id: str
    year: int
    name_zh: str
    name_en: str
    taiwan_track_category: str
    birth_lon: Optional[float]
    birth_lat: Optional[float]
    max_sustained_wind_ms: Optional[float]
    min_pressure: Optional[float]
    max_intensity_class: Optional[str]
    landfall_location: Optional[str]
    movement_summary: Optional[str]
    disaster_summary: Optional[str]
    track: pd.DataFrame = field(
        repr=False
    )  # timestamp_utc, lat, lon, wind_kt, pressure_mb


class DataLoader:
    """
    加載 processed 資料集
    """

    def __init__(self, processed_dir: str = "data/typhoon/preprocessed"):
        self.processed_dir = Path(processed_dir)
        self._records: list[TyphoonRecord] = []
        self._index: dict[str, TyphoonRecord] = {}

    @property
    def records(self) -> list[TyphoonRecord]:
        if not self._records:
            raise ValueError("尚未載入資料，請先呼叫 load()")
        return self._records

    def load(self) -> "DataLoader":
        """載入完整資料集"""
        path = self.processed_dir / "typhoons_with_tracks.json"
        if not path.exists():
            raise FileNotFoundError(
                f"找不到資料集：{path}\n請先執行 python scripts/build_dataset.py"
            )

        with open(path, "r", encoding="utf-8") as f:
            dataset = json.load(f)

        self._records = []
        self._index = {}

        for t in dataset["typhoons"]:
            track_df = pd.DataFrame(t["track"])
            if "timestamp_utc" in track_df.columns:
                track_df["timestamp_utc"] = pd.to_datetime(track_df["timestamp_utc"])

            rec = TyphoonRecord(
                typhoon_id=t["typhoon_id"],
                year=t["year"],
                name_zh=t["name_zh"],
                name_en=t["name_en"],
                taiwan_track_category=t["taiwan_track_category"],
                birth_lon=t["birth_location"]["longitude"],
                birth_lat=t["birth_location"]["latitude"],
                max_sustained_wind_ms=t.get("max_sustained_wind_ms"),
                min_pressure=t.get("min_pressure"),
                max_intensity_class=t.get("max_intensity_class"),
                landfall_location=t.get("landfall_location"),
                movement_summary=t.get("movement_summary"),
                disaster_summary=t.get("disaster_summary"),
                track=track_df,
            )
            self._records.append(rec)
            self._index[rec.typhoon_id] = rec

        print(f"✓ 已載入 {len(self._records)} 筆颱風資料")
        return self

    def get(self, typhoon_id: str) -> TyphoonRecord:
        """按 ID 取得颱風"""
        if typhoon_id not in self._index:
            raise KeyError(f"找不到颱風：{typhoon_id}")
        return self._index[typhoon_id]

    def get_all_ids(self) -> list[str]:
        return [r.typhoon_id for r in self.records]

    def get_by_category(self, category: str) -> list[TyphoonRecord]:
        """按侵臺路徑分類篩選"""
        return [r for r in self.records if r.taiwan_track_category == category]

    def get_categories(self) -> list[str]:
        """取得所有路徑分類"""
        return sorted(set(r.taiwan_track_category for r in self.records))

    def to_overview_dataframe(self) -> pd.DataFrame:
        """轉換為概覽 DataFrame（不含路徑）"""
        rows = []
        for r in self.records:
            rows.append(
                {
                    "typhoon_id": r.typhoon_id,
                    "year": r.year,
                    "name_zh": r.name_zh,
                    "name_en": r.name_en,
                    "taiwan_track_category": r.taiwan_track_category,
                    "birth_lon": r.birth_lon,
                    "birth_lat": r.birth_lat,
                    "max_sustained_wind_ms": r.max_sustained_wind_ms,
                    "min_pressure": r.min_pressure,
                    "max_intensity_class": r.max_intensity_class,
                    "landfall_location": r.landfall_location,
                    "track_point_count": len(r.track),
                }
            )
        return pd.DataFrame(rows)
