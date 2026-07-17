"""
下載所有侵臺颱風事件的 ERA5 降水資料（total_precipitation）。

資料來源：
  - ERA5 reanalysis-era5-single-levels（CDS / ecmwf-datastores API）
  - 認證資訊由專案根目錄的 .env 提供：ERA5_URL, ERA5_KEY

輸出：
  - 每個颱風獨立子目錄：
      data/typhoon/raw/typhoon_era5_precipitation/<typhoon_id>/
  - 依「年-月」切分的 NetCDF 檔（跨月事件會有多個檔）：
      <typhoon_id>_<YYYYMM>.nc
  - 時間解析度：每小時（00:00 ~ 23:00，共 24 個時刻）
  - 空間範圍（area, [N, W, S, E]）：[26.5, 118, 20, 123.5]

用法：
    python scripts/download_era5_precipitation.py                # 下載全部颱風
    python scripts/download_era5_precipitation.py --ids 195807 201709
    python scripts/download_era5_precipitation.py --limit 5      # 只下載前 5 個
    python scripts/download_era5_precipitation.py --overwrite    # 覆蓋既有檔案
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import cdsapi

# 確保在 Windows（cp950）終端機也能輸出 emoji / 中文
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CLEANED_JSON = PROJECT_ROOT / "data" / "typhoon" / "cleaned" / "typhoons_cleaned.json"
OUTPUT_DIR = PROJECT_ROOT / "data" / "typhoon" / "raw" / "typhoon_era5_precipitation"

DATASET = "reanalysis-era5-single-levels"
# ERA5 area 格式為 [North, West, South, East]
AREA = [26.5, 118, 20, 123.5]
ALL_HOURS = [f"{h:02d}:00" for h in range(24)]


def load_env(env_path: Path) -> dict[str, str]:
    """簡單解析 .env（避免額外依賴）。"""
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def parse_track_time(value: str) -> datetime:
    """解析 track timestamp（如 '1958-07-11T00:00:00Z'）。"""
    return datetime.strptime(value.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")


def get_event_date_range(typhoon: dict) -> tuple[datetime, datetime]:
    """取得颱風事件的起訖日期（以 track 時間戳為準）。"""
    times = [
        parse_track_time(pt["timestamp_utc"])
        for pt in typhoon.get("track", [])
        if pt.get("timestamp_utc")
    ]
    if not times:
        # 後備：使用 genesis / dissipation 時間
        genesis = datetime.strptime(typhoon["genesis_time"], "%Y-%m-%d %H:%M:%S")
        dissipation = datetime.strptime(
            typhoon["dissipation_time"], "%Y-%m-%d %H:%M:%S"
        )
        return genesis, dissipation
    return min(times), max(times)


def group_days_by_month(
    start: datetime, end: datetime
) -> dict[tuple[int, int], list[int]]:
    """將起訖區間內的每一天，依 (year, month) 分組。"""
    groups: dict[tuple[int, int], set[int]] = {}
    day = datetime(start.year, start.month, start.day)
    last = datetime(end.year, end.month, end.day)
    while day <= last:
        groups.setdefault((day.year, day.month), set()).add(day.day)
        day += timedelta(days=1)
    return {key: sorted(days) for key, days in groups.items()}


def build_request(year: int, month: int, days: list[int]) -> dict:
    return {
        "product_type": ["reanalysis"],
        "variable": ["total_precipitation"],
        "year": [str(year)],
        "month": [f"{month:02d}"],
        "day": [f"{d:02d}" for d in days],
        "time": ALL_HOURS,
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": AREA,
    }


def download_typhoon(client: cdsapi.Client, typhoon: dict, overwrite: bool) -> None:
    typhoon_id = typhoon["typhoon_id"]
    name = typhoon.get("name_en") or typhoon.get("name_zh") or ""
    start, end = get_event_date_range(typhoon)
    month_groups = group_days_by_month(start, end)

    typhoon_dir = OUTPUT_DIR / typhoon_id
    typhoon_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"\n🌀 {typhoon_id} {name} | {start:%Y-%m-%d} ~ {end:%Y-%m-%d} "
        f"| {len(month_groups)} 個月份區塊"
    )

    for (year, month), days in sorted(month_groups.items()):
        target = typhoon_dir / f"{typhoon_id}_{year}-{month:02d}.nc"
        if target.exists() and not overwrite:
            print(f"   ⏭️  已存在，略過：{target.name}")
            continue

        request = build_request(year, month, days)
        print(
            f"   ⬇️  下載 {year}-{month:02d}（{len(days)} 天 × 24 小時）"
            f" -> {target.name}"
        )
        try:
            client.retrieve(DATASET, request).download(str(target))
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ 下載失敗 {target.name}：{exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="下載颱風事件的 ERA5 降水資料")
    parser.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="只下載指定的颱風編號（預設全部）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="限制下載的颱風數量（依序）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆蓋既有的 NetCDF 檔案",
    )
    args = parser.parse_args()

    env = load_env(PROJECT_ROOT / ".env")
    url = env.get("ERA5_URL") or os.getenv("ERA5_URL")
    key = env.get("ERA5_KEY") or os.getenv("ERA5_KEY")
    if not url or not key:
        print("❌ 找不到 ERA5_URL / ERA5_KEY，請確認 .env 設定。")
        return 1

    if not CLEANED_JSON.exists():
        print(f"❌ 找不到颱風清單：{CLEANED_JSON}")
        return 1

    data = json.loads(CLEANED_JSON.read_text(encoding="utf-8"))
    typhoons = data.get("typhoons", [])

    if args.ids:
        wanted = set(args.ids)
        typhoons = [t for t in typhoons if t["typhoon_id"] in wanted]
    if args.limit is not None:
        typhoons = typhoons[: args.limit]

    if not typhoons:
        print("⚠️  沒有符合條件的颱風。")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = cdsapi.Client(url=url, key=key)

    print("=" * 60)
    print(f"🌧️  ERA5 降水資料下載 | 共 {len(typhoons)} 個颱風")
    print(f"📁 輸出目錄：{OUTPUT_DIR}")
    print("=" * 60)

    for typhoon in typhoons:
        download_typhoon(client, typhoon, args.overwrite)

    print("\n✅ 全部處理完成！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
