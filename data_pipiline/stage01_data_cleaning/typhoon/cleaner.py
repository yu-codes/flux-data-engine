"""
數據清理模組
職責：從原始資料 (xlsx + IBTrACS JSON) 清理與標準化，輸出到 cleaned 目錄
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime


RAW_DIR = Path("data/typhoon/raw")
IBTRACS_DIR = RAW_DIR / "typhoon_information_ibtracs"
OVERVIEW_FILE = RAW_DIR / "typhoon_information_overview.xlsx"
CLEANED_DIR = Path("data/typhoon/cleaned")


def load_overview() -> pd.DataFrame:
    return pd.read_excel(OVERVIEW_FILE)


def filter_typhoons_with_track_category(df: pd.DataFrame) -> pd.DataFrame:
    mask = df["侵臺路徑分類"].notna() & (df["侵臺路徑分類"] != "---")
    filtered = df[mask].copy()
    print(f"✓ 篩選完成：{len(filtered)} / {len(df)} 筆颱風有侵臺路徑分類")
    return filtered


def load_ibtracs_track(year: int, typhoon_id: str) -> list | None:
    json_path = (
        IBTRACS_DIR / str(year) / str(typhoon_id) / "ibtracs_position_intensity.json"
    )
    if not json_path.exists():
        return None
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("position_intensity", None)


def parse_wind_speed(raw) -> float | None:
    if pd.isna(raw):
        return None
    try:
        return float(str(raw).split("(")[0].strip())
    except (ValueError, IndexError):
        return None


def build_typhoon_record(row: pd.Series) -> dict | None:
    typhoon_id = str(row["颱風編號"])
    year = int(row["年份"])

    track = load_ibtracs_track(year, typhoon_id)
    if track is None or len(track) == 0:
        return None

    return {
        "typhoon_id": typhoon_id,
        "year": year,
        "name_zh": row["中文名稱"],
        "name_en": row["英文名稱"],
        "genesis_time": str(row["生成時間"]) if pd.notna(row["生成時間"]) else None,
        "dissipation_time": str(row["消散時間"]) if pd.notna(row["消散時間"]) else None,
        "birth_location": {
            "longitude": float(row["生成經度"]) if pd.notna(row["生成經度"]) else None,
            "latitude": float(row["生成緯度"]) if pd.notna(row["生成緯度"]) else None,
        },
        "max_intensity_value": (
            float(row["最大強度值"]) if pd.notna(row["最大強度值"]) else None
        ),
        "max_intensity_class": row["最大強度"] if pd.notna(row["最大強度"]) else None,
        "max_sustained_wind_ms": parse_wind_speed(row["近中心最大風速"]),
        "min_pressure": float(row["最低氣壓"]) if pd.notna(row["最低氣壓"]) else None,
        "taiwan_track_category": str(row["侵臺路徑分類"]),
        "landfall_location": row["登陸地段"] if pd.notna(row["登陸地段"]) else None,
        "movement_summary": row["動態"] if pd.notna(row["動態"]) else None,
        "disaster_summary": row["災情"] if pd.notna(row["災情"]) else None,
        "warning_report_count": (
            str(row["發布報數"]) if pd.notna(row["發布報數"]) else None
        ),
        "track_point_count": len(track),
        "track": track,
    }


def clean_and_save(output_dir: Path = CLEANED_DIR):
    """執行清理流程，輸出到 cleaned 目錄"""
    output_dir.mkdir(parents=True, exist_ok=True)

    print("📂 載入颱風總覽...")
    overview_df = load_overview()

    print("🔍 篩選有侵臺路徑分類的颱風...")
    filtered_df = filter_typhoons_with_track_category(overview_df)

    matched_df = filtered_df[filtered_df["IBTrACS是否匹配"] == "是"].copy()
    print(f"✓ 有 IBTrACS 路徑資料的：{len(matched_df)} 筆")

    print("🔨 構建清理後資料...")
    records = []
    skipped = []
    for _, row in matched_df.iterrows():
        record = build_typhoon_record(row)
        if record is not None:
            records.append(record)
        else:
            skipped.append(f"{row['颱風編號']} {row['英文名稱']} ({row['年份']})")

    if skipped:
        print(f"⚠ 跳過 {len(skipped)} 筆（無路徑資料）")

    # 儲存清理後資料
    full_path = output_dir / "typhoons_cleaned.json"
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": datetime.now().isoformat(),
                "description": "清理後的侵臺颱風資料（含 IBTrACS 路徑）",
                "typhoon_count": len(records),
                "typhoons": records,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"✓ 清理後資料已儲存：{full_path} ({len(records)} 筆)")
    return records
