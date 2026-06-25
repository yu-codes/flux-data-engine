"""資料載入：讀取 typhoons_overview.json，整理為記錄清單與標籤對照表。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

_RAINFALL_MISSING_TOKENS = ("", "---", "nan", "none", "na", "n/a")


def _parse_rainfall(value):
    """將降水欄位解析為 float；無效值回傳 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and np.isnan(value)) else float(value)
    text = str(value).strip()
    if text.lower() in _RAINFALL_MISSING_TOKENS:
        return None
    match = re.search(r"-?\d+(\.\d+)?", text)
    return float(match.group()) if match else None


def resolve_data_path(data_path, base_directory=None):
    """解析資料檔路徑：先試原值，再試 base_directory 與其上層目錄。

    base_directory 預設為主腳本所在目錄；找不到時拋出 FileNotFoundError。
    """
    candidates = [Path(data_path)]
    if base_directory is not None:
        base_directory = Path(base_directory)
        candidates.append(base_directory / data_path)
        candidates.append(base_directory.parent / data_path)
    candidates.append(Path.cwd() / data_path)
    candidates.append(Path.cwd().parent / data_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"找不到資料檔：{data_path}（已嘗試 {len(candidates)} 個位置）"
    )


def load_records(data_path, base_directory=None):
    """載入颱風資料，保留有路徑分類且軌跡點 >=2 的颱風。

    回傳：(records, categories_by_id)
      - records           : list[dict]，每筆含 id/year/name_zh/name_en/category/landfall/rainfall/track
      - categories_by_id  : dict[id -> category]
    """
    resolved_path = resolve_data_path(data_path, base_directory)
    with open(resolved_path, encoding="utf-8") as file:
        raw = json.load(file)
    typhoons = raw["typhoons"] if isinstance(raw, dict) else raw

    records = []
    for typhoon in typhoons:
        category = str(typhoon.get("taiwan_track_category", "") or "").strip()
        points = (typhoon.get("path") or {}).get("position_intensity") or []
        if not category or len(points) < 2:
            continue
        track_dataframe = pd.DataFrame(points)
        if "timestamp_utc" in track_dataframe.columns:
            track_dataframe["timestamp_utc"] = pd.to_datetime(
                track_dataframe["timestamp_utc"], errors="coerce", utc=True
            )
        records.append(
            {
                "id": typhoon["typhoon_id"],
                "year": int(typhoon["year"]),
                "name_zh": typhoon.get("name_zh", ""),
                "name_en": typhoon.get("name_en", ""),
                "category": category,
                "landfall": typhoon.get("landfall_location"),
                "rainfall": {
                    "tn": _parse_rainfall(typhoon.get("event_rain_tn")),
                    "kh": _parse_rainfall(typhoon.get("event_rain_kh")),
                },
                "track": track_dataframe,
            }
        )
    categories_by_id = {record["id"]: record["category"] for record in records}
    return records, categories_by_id
