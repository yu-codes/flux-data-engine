"""
數據加載模組
職責：讀取統一資料源 typhoons_overview.json，提供統一的資料存取介面

資料源（單一來源）：data/typhoon/preprocessed/typhoons_overview.json
  - 每筆颱風的軌跡位於 path.position_intensity
  - 事件降水位於 event_rain_tn / event_rain_kh（依地區）
  - 生成位置為 genesis_longitude / genesis_latitude

載入範圍：具有路徑點且有明確侵臺路徑分類（1-9 或「特殊」）的颱風，
與舊資料集 typhoons_with_tracks.json 的 207 筆評估池一致。
"""

import json
import re
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .regions import RAINFALL_REGIONS, region_field

DATASET_FILENAME = "typhoons_overview.json"

# 視為「無資料」的降水字串（含亂碼編碼的「該區無資料」）
_RAIN_NA_TOKENS = ("", "---", "nan", "none", "na", "n/a")


def _parse_rain(val) -> Optional[float]:
    """解析 event_rain 欄位：數值→float，None/空字串/「該區無資料」→ None"""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        v = float(val)
        return v if not np.isnan(v) else None
    s = str(val).strip()
    if s.lower() in _RAIN_NA_TOKENS:
        return None
    # 抽取字串中的數值（容忍單位/雜訊）
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def _parse_wind_ms(val) -> Optional[float]:
    """從 'max_wind_near_center'（如 '30 (公尺/秒)'）解析近中心風速 (m/s)"""
    if val is None:
        return None
    m = re.search(r"-?\d+(\.\d+)?", str(val))
    return float(m.group()) if m else None


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
    event_rain: dict[str, Optional[float]]  # {region_code: mm}，依 RAINFALL_REGIONS
    track: pd.DataFrame = field(
        repr=False
    )  # timestamp_utc, latitude, longitude, wind_kt, pressure_mb

    def get_rainfall(self, region: str) -> Optional[float]:
        """取得指定地區的事件降水量 (mm)，無資料回傳 None"""
        return self.event_rain.get(region)


def _rehydrate(record: dict) -> dict:
    """Undo the flattening a tabular round trip applies.

    A nested object becomes JSON text when it passes through a table, so a
    record read back from a Dataset carries `path` as a string where a record
    read from the file carries a dict. Parsing it back here means the rest of
    this loader cannot tell which route the record took.
    """
    out = dict(record)
    for key in ("path", "rainfall", "impact"):
        value = out.get(key)
        if isinstance(value, str) and value.strip().startswith(("{", "[")):
            try:
                out[key] = json.loads(value)
            except ValueError:
                out[key] = {}
    return out


class DataLoader:
    """加載統一資料集 typhoons_overview.json"""

    def __init__(
        self,
        processed_dir: str = "data/typhoon/preprocessed",
        filename: str = DATASET_FILENAME,
    ):
        self.processed_dir = Path(processed_dir)
        self.filename = filename
        self._records: list[TyphoonRecord] = []
        self._index: dict[str, TyphoonRecord] = {}

    @property
    def records(self) -> list[TyphoonRecord]:
        if not self._records:
            raise ValueError("尚未載入資料，請先呼叫 load()")
        return self._records

    def load(self) -> "DataLoader":
        """載入完整資料集（僅保留有軌跡且有路徑分類的颱風）"""
        path = self.processed_dir / self.filename
        if not path.exists():
            raise FileNotFoundError(f"找不到資料集：{path}")

        with open(path, "r", encoding="utf-8") as f:
            dataset = json.load(f)

        # 統一資料源為扁平 list
        typhoons = dataset["typhoons"] if isinstance(dataset, dict) else dataset
        return self.load_records(typhoons)

    def load_records(self, typhoons: list[dict]) -> "DataLoader":
        """Load from records the caller already has.

        The same parsing as `load()`, without the file. This is what lets the
        engine read the platform's own Dataset - the one the seeder ingested
        from this very file - instead of reaching past the platform to the
        disk. Nested fields survive ingestion as JSON text, so a record that
        came back through a table is re-parsed here rather than assumed.
        """
        self._records = []
        self._index = {}

        for raw in typhoons:
            t = _rehydrate(raw)
            category = str(t.get("taiwan_track_category", "") or "").strip()
            points = (t.get("path") or {}).get("position_intensity") or []

            # 過濾：需有明確路徑分類且具備軌跡點（對齊 207 筆評估池）
            if not category or len(points) < 2:
                continue

            track_df = pd.DataFrame(points)
            if "timestamp_utc" in track_df.columns:
                track_df["timestamp_utc"] = pd.to_datetime(
                    track_df["timestamp_utc"], errors="coerce", utc=True
                )

            event_rain = {
                code: _parse_rain(t.get(region_field(code)))
                for code in RAINFALL_REGIONS
            }

            rec = TyphoonRecord(
                typhoon_id=t["typhoon_id"],
                year=int(t["year"]),
                name_zh=t.get("name_zh", ""),
                name_en=t.get("name_en", ""),
                taiwan_track_category=category,
                birth_lon=t.get("genesis_longitude"),
                birth_lat=t.get("genesis_latitude"),
                max_sustained_wind_ms=_parse_wind_ms(t.get("max_wind_near_center")),
                min_pressure=t.get("min_pressure"),
                max_intensity_class=t.get("maximum_intensity"),
                landfall_location=t.get("landfall_location"),
                movement_summary=t.get("movement_summary"),
                disaster_summary=t.get("disaster_summary"),
                event_rain=event_rain,
                track=track_df,
            )
            self._records.append(rec)
            self._index[rec.typhoon_id] = rec

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
            row = {
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
            for code in RAINFALL_REGIONS:
                row[f"event_rain_{code}"] = r.event_rain.get(code)
            rows.append(row)
        return pd.DataFrame(rows)
