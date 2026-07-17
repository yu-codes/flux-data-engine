"""
建立「颱風降水類比集合」資料庫（Analog Ensemble DB）。

用途：
  將已下載的 ERA5 每小時總降水（tp）與各颱風 best-track 對齊，
  彙整成一個緊湊的資料庫，供後端在「即時預測」時，依查詢颱風的位置，
  以類比集合（Analog Ensemble）估計台灣各網格的降水機率分布。

科學依據：
  - 時間對齊：best-track（3 小時）以時間線性內插到每小時，對齊 ERA5 每小時場。
  - 空間對齊：降水保留在「絕對台灣網格」（保留地形雨特徵），以颱風中心位置為條件。
  - 這等同於「以颱風位置為條件的降水氣候學」(conditional climatology)，
    是 R-CLIPER 降水氣候模式與 Analog Ensemble (Delle Monache et al., 2013) 的結合。

輸出：
  data/typhoon/preprocessed/precip_analog.npz
    - grid_lat  (Ny,)          緯度（由南到北遞增）
    - grid_lon  (Nx,)          經度（由西到東遞增）
    - storm_lat (H,)           每個樣本時刻的颱風中心緯度
    - storm_lon (H,)           每個樣本時刻的颱風中心經度
    - storm_wind(H,)           每個樣本時刻的近中心風速 (kt)，缺值為 NaN
    - field     (H, Ny, Nx)    每小時降水場 (uint16 = mm/hr × 100)
    - typhoon_id(H,)           樣本所屬颱風編號
  data/typhoon/preprocessed/precip_analog_meta.json  （摘要）

用法：
    python scripts/build_precip_composite.py            # 全部颱風
    python scripts/build_precip_composite.py --limit 5  # 只處理前 5 個（測試）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

try:
    import xarray as xr
except ImportError:  # pragma: no cover
    print("需要 xarray 與 netcdf4：pip install xarray netcdf4")
    raise

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CLEANED_JSON = PROJECT_ROOT / "data" / "typhoon" / "cleaned" / "typhoons_cleaned.json"
ERA5_DIR = PROJECT_ROOT / "data" / "typhoon" / "raw" / "typhoon_era5_precipitation"
OUT_NPZ = PROJECT_ROOT / "data" / "typhoon" / "preprocessed" / "precip_analog.npz"
OUT_META = (
    PROJECT_ROOT / "data" / "typhoon" / "preprocessed" / "precip_analog_meta.json"
)

# 台灣中心（用於篩選「可能影響台灣」的時刻）
TW_LAT, TW_LON = 23.7, 121.0
# 只保留颱風中心距台灣中心 <= KEEP_RADIUS_KM 的時刻（涵蓋接近過程，含乾樣本）
KEEP_RADIUS_KM = 1200.0
# 降水量化上限（mm/hr）；uint16 存 mm/hr × 100
SCALE = 100.0
CAP_MM = 650.0


def haversine_km(lat1, lon1, lat2, lon2):
    """兩點大圓距離 (km)，可向量化。"""
    r = 6371.0
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = np.radians(np.asarray(lat2) - np.asarray(lat1))
    dlmb = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def load_era5_hourly(typhoon_id: str):
    """
    讀取某颱風所有月份的 ERA5 檔，合併為每小時降水場。

    回傳 (times ns 陣列, lat 遞增, lon 遞增, tp_mm (T,Ny,Nx)) 或 None。
    """
    tdir = ERA5_DIR / typhoon_id
    files = sorted(tdir.glob(f"{typhoon_id}_*.nc"))
    if not files:
        return None

    # 逐檔讀取後沿時間串接（避免 open_mfdataset 對 dask 的依賴）
    parts = []
    lat = lon = None
    for f in files:
        ds = xr.open_dataset(f)
        tname = "valid_time" if "valid_time" in ds else "time"
        ds = ds.sortby("latitude").sortby("longitude")  # 緯度由南到北遞增
        tp = (ds["tp"].values * 1000.0).astype(np.float32)  # (T,Ny,Nx) mm/hr
        t = ds[tname].values.astype("datetime64[ns]")
        lat = ds["latitude"].values.astype(np.float64)
        lon = ds["longitude"].values.astype(np.float64)
        ds.close()
        parts.append((t, tp))

    times = np.concatenate([p[0] for p in parts])
    tp_mm = np.concatenate([p[1] for p in parts], axis=0)
    # 依時間排序並去重（跨月檔可能有重疊）
    order = np.argsort(times)
    times, tp_mm = times[order], tp_mm[order]
    _, uniq = np.unique(times, return_index=True)
    times, tp_mm = times[uniq], tp_mm[uniq]
    return times, lat, lon, np.nan_to_num(tp_mm, nan=0.0)


def interp_track_hourly(track: list[dict], times: np.ndarray):
    """
    將 best-track 內插到給定的每小時時間點。

    回傳 (lat(H,), lon(H,), wind(H,)) 對齊 times；times 超出軌跡範圍者以端點外推。
    """
    t_ns = np.array(
        [np.datetime64(p["timestamp_utc"].replace("Z", ""), "ns") for p in track]
    )
    order = np.argsort(t_ns)
    t_ns = t_ns[order]
    lat = np.array([track[i]["latitude"] for i in order], dtype=float)
    lon = np.array([track[i]["longitude"] for i in order], dtype=float)
    wind = np.array([track[i].get("wind_kt", np.nan) for i in order], dtype=float)

    t_sec = t_ns.astype("datetime64[s]").astype(np.float64)
    q_sec = times.astype("datetime64[s]").astype(np.float64)
    lat_i = np.interp(q_sec, t_sec, lat)
    lon_i = np.interp(q_sec, t_sec, lon)
    # 風速可能全 NaN；有值才內插
    if np.isfinite(wind).sum() >= 2:
        good = np.isfinite(wind)
        wind_i = np.interp(q_sec, t_sec[good], wind[good])
    else:
        wind_i = np.full(q_sec.shape, np.nan)
    # 軌跡時間範圍外的時刻標記（不外推太遠）
    in_span = (q_sec >= t_sec[0] - 3600) & (q_sec <= t_sec[-1] + 3600)
    return lat_i, lon_i, wind_i, in_span


def process_typhoon(typhoon: dict, ref_grid: dict):
    """處理單一颱風，回傳樣本 (storm_lat, storm_lon, wind, field_uint16) 或 None。"""
    tid = typhoon["typhoon_id"]
    track = typhoon.get("track", [])
    if len(track) < 2:
        return None

    era = load_era5_hourly(tid)
    if era is None:
        return None
    times, lat, lon, tp_mm = era

    # 建立/校驗參考網格（所有颱風的網格必須一致）
    if ref_grid.get("lat") is None:
        ref_grid["lat"] = lat
        ref_grid["lon"] = lon
    else:
        if not (
            np.array_equal(lat, ref_grid["lat"])
            and np.array_equal(lon, ref_grid["lon"])
        ):
            # 網格不一致則內插到參考網格
            da = xr.DataArray(
                tp_mm,
                dims=("time", "latitude", "longitude"),
                coords={"time": times, "latitude": lat, "longitude": lon},
            )
            da = da.interp(latitude=ref_grid["lat"], longitude=ref_grid["lon"])
            tp_mm = np.nan_to_num(da.values, nan=0.0)
            lat, lon = ref_grid["lat"], ref_grid["lon"]

    lat_i, lon_i, wind_i, in_span = interp_track_hourly(track, times)

    # 篩選：軌跡時間範圍內 + 距台灣 <= KEEP_RADIUS_KM
    dist = haversine_km(lat_i, lon_i, TW_LAT, TW_LON)
    keep = in_span & (dist <= KEEP_RADIUS_KM)
    if not keep.any():
        return None

    fields = tp_mm[keep]  # (h,Ny,Nx) mm/hr
    fields = np.clip(fields, 0.0, CAP_MM)
    fields_u16 = np.round(fields * SCALE).astype(np.uint16)

    return {
        "storm_lat": lat_i[keep].astype(np.float32),
        "storm_lon": lon_i[keep].astype(np.float32),
        "storm_wind": wind_i[keep].astype(np.float32),
        "field": fields_u16,
        "n": int(keep.sum()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="建立颱風降水類比集合資料庫")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    data = json.loads(CLEANED_JSON.read_text(encoding="utf-8"))
    typhoons = data.get("typhoons", [])
    if args.limit:
        typhoons = typhoons[: args.limit]

    print(f"處理 {len(typhoons)} 個颱風 …")
    ref_grid = {"lat": None, "lon": None}
    all_lat, all_lon, all_wind, all_field, all_tid = [], [], [], [], []
    used = 0

    for i, ty in enumerate(typhoons, 1):
        tid = ty["typhoon_id"]
        try:
            res = process_typhoon(ty, ref_grid)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ {tid} 失敗：{exc}")
            continue
        if res is None:
            continue
        all_lat.append(res["storm_lat"])
        all_lon.append(res["storm_lon"])
        all_wind.append(res["storm_wind"])
        all_field.append(res["field"])
        all_tid.append(np.array([tid] * res["n"]))
        used += 1
        if i % 20 == 0 or i == len(typhoons):
            total_h = sum(len(x) for x in all_lat)
            print(f"  [{i}/{len(typhoons)}] 已用 {used} 颱風，累積 {total_h} 小時樣本")

    if not all_field:
        print("❌ 沒有可用樣本")
        return 1

    storm_lat = np.concatenate(all_lat)
    storm_lon = np.concatenate(all_lon)
    storm_wind = np.concatenate(all_wind)
    field = np.concatenate(all_field, axis=0)
    typhoon_id = np.concatenate(all_tid)

    OUT_NPZ.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT_NPZ,
        grid_lat=ref_grid["lat"].astype(np.float32),
        grid_lon=ref_grid["lon"].astype(np.float32),
        storm_lat=storm_lat,
        storm_lon=storm_lon,
        storm_wind=storm_wind,
        field=field,
        typhoon_id=typhoon_id,
        scale=np.float32(SCALE),
    )

    meta = {
        "n_typhoons_used": used,
        "n_hours": int(field.shape[0]),
        "grid_shape": [int(field.shape[1]), int(field.shape[2])],
        "grid_lat_range": [float(ref_grid["lat"].min()), float(ref_grid["lat"].max())],
        "grid_lon_range": [float(ref_grid["lon"].min()), float(ref_grid["lon"].max())],
        "keep_radius_km": KEEP_RADIUS_KM,
        "scale": SCALE,
        "field_units": "mm/hr (uint16 = mm/hr * scale)",
        "npz_size_mb": round(OUT_NPZ.stat().st_size / 1e6, 1),
    }
    OUT_META.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n✅ 完成：{used} 颱風、{field.shape[0]} 小時樣本")
    print(f"   網格 {field.shape[1]}×{field.shape[2]}，檔案 {meta['npz_size_mb']} MB")
    print(f"   輸出：{OUT_NPZ}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
