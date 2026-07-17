"""
颱風降水類比集合模式（Track-relative Analog Ensemble）。

依查詢颱風的中心位置，於歷史 ERA5 降水資料庫中找出「颱風位於相近位置」的
所有歷史時刻，以距離高斯核加權，估計台灣各網格的：
  - 降水機率 P(rain >= τ)（多個門檻）
  - 期望降水強度 E[rain] (mm/hr)

科學依據：
  - Analog Ensemble：Delle Monache et al. (2013), MWR。
  - 以颱風位置為條件的降水氣候學（conditional climatology），概念同 R-CLIPER。
  - 降水保留在絕對台灣網格，保留地形降水特徵（中央山脈效應）。

資料庫由 scripts/build_precip_composite.py 產生（precip_analog.npz）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

# 預設降水機率門檻 (mm/hr) — 對應每小時降水強度等級
DEFAULT_THRESHOLDS = [1.0, 5.0, 10.0, 20.0, 30.0]
# 預設高斯核頻寬 (km)：颱風位置相近程度的空間尺度
DEFAULT_BANDWIDTH_KM = 150.0
# 硬截斷半徑 = 頻寬 × 此倍數（超過則權重視為 0，加速計算）
CUTOFF_FACTOR = 3.0
# 有效樣本數過少時的下限（低於此僅回報，仍計算）
MIN_EFFECTIVE = 5.0
EARTH_R_KM = 6371.0


def _haversine_km(lat1, lon1, lat2, lon2):
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


class PrecipAnalogModel:
    """載入類比集合資料庫，提供依位置查詢降水機率分布的能力。"""

    def __init__(self, npz_path: str | Path):
        self.npz_path = Path(npz_path)
        self.loaded = False
        self.grid_lat = None
        self.grid_lon = None
        self.storm_lat = None
        self.storm_lon = None
        self.storm_wind = None
        self.field = None  # uint16 (H, Ny, Nx)
        self.scale = 100.0
        self.typhoon_id = None

    def load(self) -> "PrecipAnalogModel":
        data = np.load(self.npz_path, allow_pickle=True)
        self.grid_lat = data["grid_lat"].astype(np.float64)
        self.grid_lon = data["grid_lon"].astype(np.float64)
        self.storm_lat = data["storm_lat"].astype(np.float64)
        self.storm_lon = data["storm_lon"].astype(np.float64)
        self.storm_wind = data["storm_wind"].astype(np.float64)
        self.field = data["field"]  # uint16
        self.scale = float(data["scale"]) if "scale" in data else 100.0
        self.typhoon_id = data["typhoon_id"] if "typhoon_id" in data else None
        self.loaded = True
        return self

    @property
    def grid_shape(self):
        return (len(self.grid_lat), len(self.grid_lon))

    def forecast_point(
        self,
        lat: float,
        lon: float,
        thresholds=DEFAULT_THRESHOLDS,
        bandwidth_km: float = DEFAULT_BANDWIDTH_KM,
        wind_kt: float | None = None,
        wind_scale_kt: float = 25.0,
    ) -> dict:
        """
        估計單一颱風位置下，台灣各網格的降水機率與期望降水。

        參數：
          lat, lon      : 颱風中心位置
          thresholds    : 降水門檻 (mm/hr) 清單
          bandwidth_km  : 位置高斯核頻寬 (km)
          wind_kt       : 近中心風速（可選）；提供時加入強度相似度加權
          wind_scale_kt : 風速相似度尺度 (kt)

        回傳 dict：
          expected      : (Ny*Nx,) 期望降水 mm/hr（已攤平）
          prob          : {threshold: (Ny*Nx,)} 各門檻超越機率
          n_analogs     : 參與計算的歷史時刻數
          n_effective   : 有效樣本數（核加權）
        """
        d = _haversine_km(self.storm_lat, self.storm_lon, lat, lon)
        cutoff = bandwidth_km * CUTOFF_FACTOR
        sel = np.where(d <= cutoff)[0]
        ny, nx = self.grid_shape
        ncell = ny * nx

        if sel.size == 0:
            return {
                "expected": np.zeros(ncell, dtype=np.float32),
                "prob": {
                    float(t): np.zeros(ncell, dtype=np.float32) for t in thresholds
                },
                "n_analogs": 0,
                "n_effective": 0.0,
            }

        dd = d[sel]
        w = np.exp(-0.5 * (dd / bandwidth_km) ** 2)
        # 強度相似度加權（可選）
        if wind_kt is not None and np.isfinite(wind_kt):
            wv = self.storm_wind[sel]
            good = np.isfinite(wv)
            wfac = np.ones_like(w)
            wfac[good] = np.exp(-0.5 * ((wv[good] - wind_kt) / wind_scale_kt) ** 2)
            w = w * wfac

        wsum = w.sum()
        if wsum <= 0:
            return {
                "expected": np.zeros(ncell, dtype=np.float32),
                "prob": {
                    float(t): np.zeros(ncell, dtype=np.float32) for t in thresholds
                },
                "n_analogs": int(sel.size),
                "n_effective": 0.0,
            }

        n_eff = float((wsum**2) / np.sum(w**2))

        # 取出選中的降水場（攤平為 (m, ncell)），以 BLAS matmul 加權求和
        fields = self.field[sel].reshape(sel.size, ncell).astype(np.float32)
        wv = w.astype(np.float32)
        inv = np.float32(1.0 / wsum)

        expected = (wv @ fields) * inv / self.scale  # (ncell,) mm/hr
        prob = {}
        for t in thresholds:
            thr_scaled = np.float32(float(t) * self.scale)
            exceed = (fields >= thr_scaled).astype(np.float32)
            prob[float(t)] = (wv @ exceed) * inv

        return {
            "expected": expected.astype(np.float32),
            "prob": prob,
            "n_analogs": int(sel.size),
            "n_effective": round(n_eff, 1),
        }

    def forecast_positions(
        self,
        positions: list[dict],
        thresholds=DEFAULT_THRESHOLDS,
        bandwidth_km: float = DEFAULT_BANDWIDTH_KM,
        use_wind: bool = True,
    ) -> list[dict]:
        """對一連串颱風位置逐一估計，回傳每個位置（frame）的結果。"""
        frames = []
        for i, p in enumerate(positions):
            wind = p.get("wind_kt") if use_wind else None
            r = self.forecast_point(
                lat=float(p["lat"]),
                lon=float(p["lon"]),
                thresholds=thresholds,
                bandwidth_km=bandwidth_km,
                wind_kt=wind,
            )
            frames.append(
                {
                    "step": i,
                    "lat": round(float(p["lat"]), 3),
                    "lon": round(float(p["lon"]), 3),
                    "wind_kt": (
                        round(float(p["wind_kt"]), 1)
                        if p.get("wind_kt") is not None
                        else None
                    ),
                    "n_analogs": r["n_analogs"],
                    "n_effective": r["n_effective"],
                    "expected": [round(float(v), 2) for v in r["expected"]],
                    "prob": {
                        str(t): [round(float(v), 3) for v in arr]
                        for t, arr in r["prob"].items()
                    },
                }
            )
        return frames


def interpolate_track(track: list[dict], steps: int) -> list[dict]:
    """
    將查詢颱風路徑（lat/lon 點列）沿折線內插成 steps 個等距位置，供動畫逐格呈現。

    以累積大圓弧長等分，風速同步線性內插。
    """
    if len(track) < 2 or steps <= len(track):
        return [
            {
                "lat": float(p["latitude"] if "latitude" in p else p["lat"]),
                "lon": float(p["longitude"] if "longitude" in p else p["lon"]),
                "wind_kt": p.get("wind_kt"),
            }
            for p in track
        ]

    lats = np.array(
        [p["latitude"] if "latitude" in p else p["lat"] for p in track], dtype=float
    )
    lons = np.array(
        [p["longitude"] if "longitude" in p else p["lon"] for p in track], dtype=float
    )
    winds = np.array([p.get("wind_kt", np.nan) for p in track], dtype=float)

    seg = _haversine_km(lats[:-1], lons[:-1], lats[1:], lons[1:])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 0:
        return [{"lat": float(lats[0]), "lon": float(lons[0]), "wind_kt": None}]

    targets = np.linspace(0.0, total, steps)
    lat_i = np.interp(targets, cum, lats)
    lon_i = np.interp(targets, cum, lons)
    if np.isfinite(winds).sum() >= 2:
        good = np.isfinite(winds)
        wind_i = np.interp(targets, cum[good], winds[good])
    else:
        wind_i = np.full(steps, np.nan)

    return [
        {
            "lat": float(la),
            "lon": float(lo),
            "wind_kt": (float(wi) if np.isfinite(wi) else None),
        }
        for la, lo, wi in zip(lat_i, lon_i, wind_i)
    ]
