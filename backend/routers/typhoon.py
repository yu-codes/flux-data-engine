"""颱風預測相關端點"""

import json
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.state import app_state, SUPPORTED_METHODS, DEFAULT_BUFFER_KM
from data_pipiline.stage00_data_ingestion.typhoon import coastline as coast
from data_pipiline.stage05_model_training.typhoon.similarity.coastline import (
    path_offset_km,
    SCORE_TAU_KM,
)
from data_pipiline.stage04_feature_engineering.typhoon.extractor import (
    TyphoonFeatureExtractor,
)
from data_pipiline.stage05_model_training.typhoon.similarity.rule_based import (
    classify_typhoon_by_rules,
)
from data_pipiline.stage05_model_training.typhoon.similarity.knn import KNNSimilarity
from data_pipiline.stage05_model_training.typhoon.mapping import (
    ImpactMapper,
    TRACK_CATEGORY_DESCRIPTION,
)
from data_pipiline.stage00_data_ingestion.typhoon.regions import (
    RAINFALL_REGIONS,
    region_codes,
    region_label,
)
from data_pipiline.stage08_downstream_analysis.typhoon.precip_analog import (
    DEFAULT_THRESHOLDS,
    DEFAULT_BANDWIDTH_KM,
    interpolate_track,
)

router = APIRouter(prefix="/api/typhoon", tags=["typhoon"])

BASE_DIR = Path(__file__).parent.parent.parent
EXPERIMENTS_DIR = BASE_DIR / "experiments" / "typhoon"
ALL_CASES_DIR = EXPERIMENTS_DIR / "all_cases"
SINGLE_CASE_DIR = EXPERIMENTS_DIR / "single_case"


# === Schemas ===
class TrackPoint(BaseModel):
    latitude: float
    longitude: float
    wind_kt: float | None = None
    pressure_mb: float | None = None


class PredictRequest(BaseModel):
    track: list[TrackPoint] = Field(..., min_length=2)
    method: str = "combined_optimized"
    k: int = Field(default=5, ge=1, le=20)
    alpha: float | None = None
    rule_weight: float | None = None
    rrf_k: int | None = None
    # 海岸線外擴緩衝半徑 (km) — coastline 方法用
    buffer_km: float | None = Field(default=None, ge=50, le=2000)
    # 降水相關
    use_rainfall: bool = False
    rainfall_region: str = "tn"
    rainfall_weight: float | None = None
    expected_rainfall: float | None = None  # 查詢颱風預期降水量（啟用降水訊號時可選）


class TrackCoord(BaseModel):
    lat: float
    lon: float
    in_range: bool = True  # 是否落在計算範圍（海岸線外擴 buffer_km）內


class SimilarTyphoon(BaseModel):
    typhoon_id: str
    name_zh: str
    name_en: str
    year: int
    category: str
    distance: float
    score: float
    # 完整軌跡（供地圖投影繪製）
    track: list[TrackCoord] = []


class RainfallStation(BaseModel):
    region: str
    label: str
    mean: float
    median: float
    min: float
    max: float
    count: int


class RainfallInfo(BaseModel):
    region: str
    region_label: str
    stations: dict[str, RainfallStation]


class PredictResponse(BaseModel):
    method: str
    predicted_category: str | None
    confidence: float
    category_votes: dict[str, float]
    similar_typhoons: list[SimilarTyphoon]
    rainfall: RainfallInfo | None = None
    charts: list[str] = []
    # 地圖投影資料
    query_track: list[TrackCoord] = []
    coastline: list[TrackCoord] = []
    buffer: list[TrackCoord] = []
    buffer_km: float | None = None
    distance_unit: str = "score"  # coastline 方法為 "km"


class RunMeta(BaseModel):
    run_id: str
    experiment: str
    run_path: str
    meta: dict


# === 預測端點 ===
@router.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    """執行颱風路徑類比預測"""
    if req.method not in SUPPORTED_METHODS:
        raise HTTPException(
            400, f"Unsupported method: {req.method}. Use one of {SUPPORTED_METHODS}"
        )

    pipeline = app_state.pipelines.get(req.method)
    if not pipeline:
        raise HTTPException(503, f"Model '{req.method}' not loaded")

    rainfall_in_use = req.use_rainfall
    if rainfall_in_use and req.rainfall_region not in RAINFALL_REGIONS:
        raise HTTPException(
            400,
            f"Unsupported rainfall_region: {req.rainfall_region}. Use one of {region_codes()}",
        )

    # 建立 track DataFrame
    track_df = pd.DataFrame([p.model_dump() for p in req.track])
    if "timestamp_utc" not in track_df.columns:
        track_df["timestamp_utc"] = pd.date_range(
            "2000-01-01", periods=len(track_df), freq="6h"
        )

    # 計算範圍（海岸線外擴 buffer_km）— 所有方法共用；改變才重算參考特徵
    buffer_km = req.buffer_km if req.buffer_km is not None else pipeline.buffer_km
    pipeline.set_buffer_km(buffer_km)

    # 預測
    distance_unit = "score"
    if req.method == "rule_based":
        rule_result = classify_typhoon_by_rules(
            track_df, landfall_location=None, buffer_km=buffer_km
        )
        predicted_cat = rule_result["predicted_category"]
        confidence = rule_result["confidence"]
        category_votes = {predicted_cat: 1.0}

        extractor = TyphoonFeatureExtractor(buffer_km=buffer_km)
        query_features = extractor.extract(typhoon_id="query", track=track_df)
        knn_sim = KNNSimilarity()
        knn_sim.fit(pipeline.features)
        sim_result = knn_sim.find_similar_by_vector(
            query_features.to_feature_vector(), k=req.k
        )
    elif req.method == "coastline":
        # 海岸線範圍內絕對位置相似度 — distances 以 km 表示
        distance_unit = "km"
        try:
            sim_result = pipeline.similarity.find_similar_by_track(
                track_df, k=req.k, buffer_km=buffer_km
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        # 以正規化距離（1 - score）做類比投票，避免 km 量級導致 exp(-d) 下溢
        norm_dists = [1.0 - s for s in sim_result.scores]
        pred = pipeline.model.predict(
            query_id="query",
            similar_ids=sim_result.similar_ids,
            distances=norm_dists,
        )
        predicted_cat = pred.get("predicted_category")
        confidence = pred.get("confidence", 0.0)
        category_votes = pred.get("category_votes", {})
    elif req.method == "coastline_rrf":
        # 絕對位置（主）+ KNN + 降水（可選）RRF 融合
        extractor = TyphoonFeatureExtractor(buffer_km=buffer_km)
        query_features = extractor.extract(typhoon_id="query", track=track_df)
        query_vec = query_features.to_feature_vector()
        use_rain = req.use_rainfall
        if hasattr(pipeline.similarity, "configure_rainfall"):
            pipeline.similarity.configure_rainfall(
                use_rainfall=use_rain,
                region=req.rainfall_region,
                weight=req.rainfall_weight if req.rainfall_weight is not None else 0.08,
            )
        track_kwargs = {"k": req.k, "buffer_km": buffer_km}
        if use_rain and req.expected_rainfall is not None:
            track_kwargs["query_rainfall"] = req.expected_rainfall
        try:
            sim_result = pipeline.similarity.find_similar_by_track(
                track_df, query_vec, **track_kwargs
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        pred = pipeline.model.predict(
            query_id="query",
            similar_ids=sim_result.similar_ids,
            distances=sim_result.distances,
        )
        predicted_cat = pred.get("predicted_category")
        confidence = pred.get("confidence", 0.0)
        category_votes = pred.get("category_votes", {})
    else:
        extractor = TyphoonFeatureExtractor(buffer_km=buffer_km)
        query_features = extractor.extract(typhoon_id="query", track=track_df)
        query_vec = query_features.to_feature_vector()

        sim_kwargs = {"k": req.k}
        if req.method == "combined_rainfall":
            sim_kwargs["query_features"] = query_features
            # 降水訊號由前端 toggle 控制（use_rainfall）
            use_rain = req.use_rainfall
            # 逐請求設定降水訊號
            if hasattr(pipeline.similarity, "configure_rainfall"):
                pipeline.similarity.configure_rainfall(
                    use_rainfall=use_rain,
                    region=req.rainfall_region,
                    weight=(
                        req.rainfall_weight if req.rainfall_weight is not None else 0.15
                    ),
                )
                if use_rain and req.expected_rainfall is not None:
                    sim_kwargs["query_rainfall"] = req.expected_rainfall
        sim_result = pipeline.similarity.find_similar_by_vector(query_vec, **sim_kwargs)
        pred = pipeline.model.predict(
            query_id="query",
            similar_ids=sim_result.similar_ids,
            distances=sim_result.distances,
        )
        predicted_cat = pred.get("predicted_category")
        confidence = pred.get("confidence", 0.0)
        category_votes = pred.get("category_votes", {})

    # 相似颱風：距離 / 相似度一律用「與查詢路徑的平均偏離 (km)」這個絕對指標，
    # 不再用各方法的相對正規化分數（避免排名第 1 永遠是 0km / 100%）。
    # 排名仍由各方法的演算法決定；此處只是更合理地呈現「有多接近」。
    similar_info = []
    for tid in sim_result.similar_ids:
        rec = pipeline.loader.get(tid)
        offset = path_offset_km(
            track_df["longitude"].values,
            track_df["latitude"].values,
            rec.track["longitude"].values,
            rec.track["latitude"].values,
            buffer_km,
        )
        similarity = (
            float(np.exp(-offset / SCORE_TAU_KM)) if np.isfinite(offset) else 0.0
        )
        similar_info.append(
            SimilarTyphoon(
                typhoon_id=tid,
                name_zh=rec.name_zh,
                name_en=rec.name_en,
                year=rec.year,
                category=rec.taiwan_track_category or "?",
                distance=round(float(offset), 1),
                score=round(similarity, 4),
                track=_track_coords(rec.track, buffer_km=buffer_km),
            )
        )
    distance_unit = "km"  # 所有方法的顯示距離皆為「平均偏離 (km)」

    # 降水分析（各地區，依類比颱風推估）
    rainfall_result = None
    if app_state.rainfall_analyzer:
        try:
            # 收集每個類比颱風各地區降水
            analog_rainfalls = []
            for si in similar_info:
                rec_rain = app_state.rainfall_analyzer.get_rainfall(si.typhoon_id)
                if rec_rain:
                    analog_rainfalls.append(rec_rain)  # {region: mm|None}

            if analog_rainfalls:
                stations: dict[str, RainfallStation] = {}
                for code in region_codes():
                    vals = [
                        ar[code] for ar in analog_rainfalls if ar.get(code) is not None
                    ]
                    if vals:
                        label = region_label(code)
                        stations[label] = RainfallStation(
                            region=code,
                            label=label,
                            mean=round(float(np.mean(vals)), 1),
                            median=round(float(np.median(vals)), 1),
                            min=round(float(min(vals)), 1),
                            max=round(float(max(vals)), 1),
                            count=len(vals),
                        )
                if stations:
                    rainfall_result = RainfallInfo(
                        region=req.rainfall_region,
                        region_label=region_label(req.rainfall_region),
                        stations=stations,
                    )
        except Exception:
            pass

    # 圖表生成
    chart_urls = []
    try:
        from data_pipiline.stage08_downstream_analysis.typhoon.plots import (
            TyphoonVisualizer,
        )
        from data_pipiline.stage00_data_ingestion.typhoon.loader import TyphoonRecord

        case_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        case_dir = SINGLE_CASE_DIR / case_timestamp
        case_dir.mkdir(parents=True, exist_ok=True)

        query_rec = TyphoonRecord(
            typhoon_id="query",
            year=2026,
            name_zh="查詢颱風",
            name_en="QUERY",
            taiwan_track_category=str(predicted_cat or "?"),
            birth_lon=None,
            birth_lat=None,
            max_sustained_wind_ms=None,
            min_pressure=None,
            max_intensity_class=None,
            landfall_location=None,
            movement_summary=None,
            disaster_summary=None,
            track=track_df,
        )
        similar_recs = []
        for si in similar_info:
            try:
                similar_recs.append(pipeline.loader.get(si.typhoon_id))
            except KeyError:
                pass

        viz = TyphoonVisualizer(str(case_dir))
        if similar_recs:
            viz.plot_prediction_example(
                query_rec, similar_recs, str(predicted_cat or ""), confidence
            )

        for img in sorted(case_dir.glob("*.png")):
            chart_urls.append(
                f"/static/experiments/typhoon/single_case/{case_timestamp}/{img.name}"
            )
    except Exception:
        pass

    # 地圖投影資料（海岸線輪廓 + 外擴緩衝 + 查詢路徑）— buffer 即實際計算範圍
    coastline_coords = [TrackCoord(**p) for p in coast.outline_lonlat()]
    buffer_coords = [TrackCoord(**p) for p in coast.buffer_polygon(buffer_km)]

    return PredictResponse(
        method=req.method,
        predicted_category=predicted_cat,
        confidence=round(confidence, 4),
        category_votes={k: round(v, 4) for k, v in category_votes.items()},
        similar_typhoons=similar_info,
        rainfall=rainfall_result,
        charts=chart_urls,
        query_track=_track_coords(track_df, buffer_km=buffer_km),
        coastline=coastline_coords,
        buffer=buffer_coords,
        buffer_km=round(buffer_km, 1),
        distance_unit=distance_unit,
    )


# === 海岸線 / 緩衝區端點 ===
@router.get("/coastline")
def get_coastline(buffer_km: float = DEFAULT_BUFFER_KM):
    """取得台灣海岸線輪廓與外擴 buffer_km 的緩衝區多邊形（供前端地圖投影）"""
    if not (50 <= buffer_km <= 2000):
        raise HTTPException(400, "buffer_km must be between 50 and 2000")
    return {
        "buffer_km": round(buffer_km, 1),
        "coastline": coast.outline_lonlat(),
        "buffer": coast.buffer_polygon(buffer_km),
    }


# === 降水機率分布（類比集合）端點 ===
class PrecipForecastRequest(BaseModel):
    # 查詢颱風路徑（將沿折線內插為 steps 個動畫格）
    track: list[TrackPoint] | None = None
    # 或直接指定位置（拖曳颱風時用單一位置）
    positions: list[TrackPoint] | None = None
    steps: int = Field(default=24, ge=2, le=80)
    thresholds: list[float] | None = None
    bandwidth_km: float = Field(default=DEFAULT_BANDWIDTH_KM, ge=30, le=600)
    use_wind: bool = True


@router.post("/precipitation_forecast")
def precipitation_forecast(req: PrecipForecastRequest):
    """
    依查詢颱風位置，回傳台灣各網格的降水機率分布（Analog Ensemble）。

    回傳每個動畫格（frame）包含颱風位置、期望降水 (mm/hr) 與各門檻的超越機率。
    前端可逐格播放（近似動畫）或拖曳颱風位置即時查詢。
    """
    model = app_state.precip_analog
    if model is None or not model.loaded:
        raise HTTPException(
            503,
            "降水類比資料庫未載入，請先執行 scripts/build_precip_composite.py",
        )

    thresholds = req.thresholds or DEFAULT_THRESHOLDS
    thresholds = sorted({float(t) for t in thresholds if t > 0})

    # 決定要估計的位置序列
    if req.positions:
        positions = [
            {"lat": p.latitude, "lon": p.longitude, "wind_kt": p.wind_kt}
            for p in req.positions
        ]
    elif req.track:
        raw = [
            {"latitude": p.latitude, "longitude": p.longitude, "wind_kt": p.wind_kt}
            for p in req.track
        ]
        positions = interpolate_track(raw, req.steps)
    else:
        raise HTTPException(400, "需要提供 track 或 positions")

    frames = model.forecast_positions(
        positions,
        thresholds=thresholds,
        bandwidth_km=req.bandwidth_km,
        use_wind=req.use_wind,
    )

    return {
        "grid_lat": [round(float(v), 4) for v in model.grid_lat],
        "grid_lon": [round(float(v), 4) for v in model.grid_lon],
        "grid_shape": list(model.grid_shape),
        "cell_deg": 0.25,
        "thresholds": thresholds,
        "bandwidth_km": req.bandwidth_km,
        "n_database_hours": int(len(model.storm_lat)),
        "coastline": coast.outline_lonlat(),
        "frames": frames,
    }


# === 實驗歷史端點 ===
@router.get("/analysis")
def get_analysis():
    """取得分析圖表列表（軌跡 / 降水）"""
    analysis_dir = EXPERIMENTS_DIR / "analysis"
    track_images = []
    rainfall_images = []
    if analysis_dir.exists():
        for img in sorted(analysis_dir.glob("*.png")):
            entry = {
                "name": img.stem,
                "url": f"/static/experiments/typhoon/analysis/{img.name}",
            }
            if "rainfall" in img.stem:
                rainfall_images.append(entry)
            else:
                track_images.append(entry)
    return {"track_images": track_images, "rainfall_images": rainfall_images}


@router.get("/categories")
def list_categories():
    """列出所有路徑分類及其描述"""
    return {
        "categories": [
            {"id": cat, "description": desc}
            for cat, desc in TRACK_CATEGORY_DESCRIPTION.items()
        ]
    }


@router.get("/runs")
def list_runs():
    """列出所有批次預測實驗"""
    runs = []
    if not ALL_CASES_DIR.exists():
        return runs

    for exp_dir in sorted(ALL_CASES_DIR.iterdir()):
        if not exp_dir.is_dir() or exp_dir.name.startswith(("_", ".")):
            continue
        pred_dir = exp_dir / "predictions"
        if not pred_dir.exists() or not pred_dir.is_dir():
            continue
        meta = _load_run_meta(pred_dir)
        run_path = f"all_cases/{exp_dir.name}/predictions"
        runs.append(
            RunMeta(
                run_id=exp_dir.name,
                experiment=exp_dir.name,
                run_path=run_path,
                meta=meta,
            )
        )

    runs.sort(key=lambda r: r.run_id, reverse=True)
    return runs


@router.get("/runs/{run_id}")
def get_run_detail(run_id: str):
    """取得特定實驗的詳細結果"""
    run_dir = ALL_CASES_DIR / run_id / "predictions"
    if not run_dir.is_dir():
        raise HTTPException(404, "Run not found")

    meta = _load_run_meta(run_dir)

    images = []
    rainfall_images = []
    for img in sorted(run_dir.glob("*.png")):
        entry = {
            "name": img.stem,
            "url": f"/static/experiments/typhoon/all_cases/{run_id}/predictions/{img.name}",
        }
        if "rainfall" in img.stem:
            rainfall_images.append(entry)
        else:
            images.append(entry)

    rainfall_data = None
    rainfall_path = run_dir / "rainfall_analysis.json"
    if rainfall_path.exists():
        with open(rainfall_path, "r", encoding="utf-8") as f:
            rainfall_data = json.load(f)

    return {
        "run_id": run_id,
        "meta": meta,
        "images": images,
        "rainfall_images": rainfall_images,
        "rainfall_data": rainfall_data,
    }


# === 輔助函式 ===
def _track_coords(
    track_df: pd.DataFrame, buffer_km: float | None = None
) -> list[TrackCoord]:
    """將軌跡 DataFrame 轉為 [{lat, lon, in_range}, ...]（供地圖投影）

    buffer_km 提供時，標記各點是否落在計算範圍（海岸線外擴 buffer_km）內，
    讓地圖可凸顯實際參與計算的路徑段。
    """
    lats = track_df["latitude"].values
    lons = track_df["longitude"].values
    valid = pd.notna(lats) & pd.notna(lons)
    lats = lats[valid].astype(float)
    lons = lons[valid].astype(float)
    if buffer_km is not None and len(lats):
        in_range = coast.distances_to_coast_km(lons, lats) <= buffer_km
    else:
        in_range = np.ones(len(lats), dtype=bool)
    return [
        TrackCoord(lat=round(float(la), 4), lon=round(float(lo), 4), in_range=bool(ir))
        for la, lo, ir in zip(lats, lons, in_range)
    ]


def _load_run_meta(run_dir: Path) -> dict:
    meta_path = run_dir / "run_meta.json"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    summary_path = run_dir / "evaluation_summary.json"
    if summary_path.exists():
        with open(summary_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"note": "No metadata found"}
