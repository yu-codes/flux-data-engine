"""
資料前處理模組
職責：將清理後的資料轉換為模型可用的格式，輸出到 preprocessed 目錄

處理流程：
  cleaned JSON → 標準化格式 JSON（含 track DataFrame 可直接載入的格式）
"""

import json
import numpy as np
from pathlib import Path
from datetime import datetime


CLEANED_DIR = Path("data/typhoon/cleaned")
PREPROCESSED_DIR = Path("data/typhoon/preprocessed")


def preprocess_and_save(
    cleaned_dir: Path = CLEANED_DIR,
    output_dir: Path = PREPROCESSED_DIR,
):
    """從 cleaned 資料建立 preprocessed 資料集"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 讀取清理後資料
    cleaned_path = cleaned_dir / "typhoons_cleaned.json"
    if not cleaned_path.exists():
        raise FileNotFoundError(
            f"找不到清理後資料：{cleaned_path}\n請先執行 01_data_cleaning"
        )

    with open(cleaned_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data["typhoons"]
    print(f"✓ 讀取 {len(records)} 筆清理後資料")

    # 完整資料集（與舊 processed 格式相容）
    full_path = output_dir / "typhoons_with_tracks.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now().isoformat(),
                "description": "侵臺颱風完整資料（含 IBTrACS 路徑）- 前處理後",
                "typhoon_count": len(records),
                "track_categories": sorted(
                    set(r["taiwan_track_category"] for r in records)
                ),
                "typhoons": records,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"✓ 完整資料集已儲存：{full_path}")

    # 索引檔
    index_records = [
        {
            "typhoon_id": r["typhoon_id"],
            "year": r["year"],
            "name_zh": r["name_zh"],
            "name_en": r["name_en"],
            "taiwan_track_category": r["taiwan_track_category"],
            "birth_lon": r["birth_location"]["longitude"],
            "birth_lat": r["birth_location"]["latitude"],
            "max_sustained_wind_ms": r["max_sustained_wind_ms"],
            "min_pressure": r["min_pressure"],
            "max_intensity_class": r["max_intensity_class"],
            "landfall_location": r["landfall_location"],
            "track_point_count": r["track_point_count"],
        }
        for r in records
    ]

    index_path = output_dir / "typhoons_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_records, f, ensure_ascii=False, indent=2)
    print(f"✓ 索引檔已儲存：{index_path}")

    # 統計摘要
    cat_counts = {}
    for r in records:
        cat = r["taiwan_track_category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    summary = {
        "total_typhoons": len(records),
        "category_distribution": dict(sorted(cat_counts.items())),
        "year_range": [
            min(r["year"] for r in records),
            max(r["year"] for r in records),
        ],
    }
    summary_path = output_dir / "dataset_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"✓ 摘要已儲存：{summary_path}")
    return summary
