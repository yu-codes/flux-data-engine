"""Typhoon analog engine.

Holds the loaded historical dataset, the extracted features and the fitted
similarity method, and answers "which historical typhoons look like this
track?". This is the original research pipeline's assembly logic, rehomed:
the tuned weights, the coastline-buffer computation window and the per-method
construction are all preserved as they were.

Fitting 200-odd typhoons takes a few seconds, so engines are cached per
(method, buffer_km) and shared across executions.
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.shared.errors import NotFoundError, ValidationError

from .algorithms import geometry as coast
from .algorithms.analog import AnalogModel
from .algorithms.baseline import BaselineSimilarity
from .algorithms.coastline import SCORE_TAU_KM, CoastlineSimilarity, path_offset_km
from .algorithms.coastline_rrf import CoastlineRRFSimilarity
from .algorithms.combined import CombinedSimilarity
from .algorithms.features import TyphoonFeatureExtractor
from .algorithms.knn import KNNSimilarity
from .algorithms.loader import DataLoader
from .algorithms.mapping import TRACK_CATEGORY_DESCRIPTION, ImpactMapper
from .algorithms.metrics import compute_category_accuracy
from .algorithms.regions import RAINFALL_REGIONS, region_codes, region_label
from .algorithms.rule_based import RuleBasedSimilarity, classify_typhoon_by_rules
from .paths import typhoon_data_dir

DATASET_FILENAME = "typhoons_overview.json"
DEFAULT_BUFFER_KM = 500.0
MIN_BUFFER_KM = 50.0
MAX_BUFFER_KM = 2000.0

#  Feature weights found by the original hyper-parameter search: emphasise
#  minimum distance to Taiwan, mean approach angle and landfall.
OPTIMIZED_FEATURE_WEIGHTS = np.array(
    [3.0, 2.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 2.5, 0.5, 0.5]
)
OPTIMIZED_DTW_WEIGHTS = np.array([1.0, 0.5, 1.0, 0.5])

METHODS: dict[str, str] = {
    "coastline_rrf": "Coastline RRF - absolute position (dominant) fused with "
                     "KNN and optional rainfall ranking",
    "coastline": "Coastline - absolute-position Chamfer distance inside the buffer",
    "combined_rainfall": "Combined RRF - KNN + DTW + rule + optional rainfall",
    "knn_optimized": "KNN over weighted 11-dimensional summary features",
    "rule_based": "CWA rule-based track classification",
    "baseline": "Random baseline (lower bound)",
}

DEFAULT_METHOD = "coastline_rrf"

#  The nine CWA landfall-track classes. Category "特殊" is excluded from
#  scoring because it is a catch-all rather than a geometric class.
VALID_CATEGORIES = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]


@dataclass
class AnalogPrediction:
    method: str
    predicted_category: str | None
    confidence: float
    category_votes: dict[str, float]
    analogs: list[dict[str, Any]]
    buffer_km: float
    rainfall: dict[str, Any] | None = None
    distance_unit: str = "km"


class TyphoonEngine:
    """One fitted (method, buffer_km) combination over the historical dataset."""

    def __init__(self, method: str = DEFAULT_METHOD, buffer_km: float = DEFAULT_BUFFER_KM):
        if method not in METHODS:
            raise ValidationError(
                f"unknown typhoon method '{method}'",
                details={"available": sorted(METHODS)},
            )
        self.method = method
        self.buffer_km = _validated_buffer(buffer_km)
        self.loader: DataLoader | None = None
        self.extractor = TyphoonFeatureExtractor(buffer_km=self.buffer_km)
        self.features: dict = {}
        self.label_dict: dict[str, str] = {}
        self.similarity = None
        self.model: AnalogModel | None = None

    # -- lifecycle ---------------------------------------------------------
    def fit(self, records: list[dict] | None = None) -> TyphoonEngine:
        """Load the historical record and build the similarity index.

        `records` is the platform's own Dataset, handed over by the execution
        service. Falling back to the bundled file keeps the offline tools -
        the ones that built that dataset in the first place - working.
        """
        if records is not None:
            self.loader = DataLoader().load_records(records)
        else:
            data_dir = typhoon_data_dir()
            if not (Path(data_dir) / DATASET_FILENAME).exists():
                raise NotFoundError(
                    f"typhoon dataset not found at {Path(data_dir) / DATASET_FILENAME}"
                )
            self.loader = DataLoader(str(data_dir), filename=DATASET_FILENAME).load()
        self.features = self.extractor.extract_all(self.loader)
        self.label_dict = ImpactMapper.build_label_dict(self.loader)
        self.similarity = self._build_similarity()
        self._fit_similarity()
        self.model = AnalogModel(label_dict=self.label_dict)
        return self

    def _build_similarity(self):
        if self.method == "coastline_rrf":
            #  Absolute position dominates (0.80) so the top matches are the
            #  tracks that visually hug the query path; KNN contributes 0.20.
            return CoastlineRRFSimilarity(
                buffer_km=self.buffer_km,
                w_coastline=0.80,
                w_knn=0.20,
                w_rainfall=0.08,
                feature_weights=OPTIMIZED_FEATURE_WEIGHTS,
                pool_size_factor=12,
                rrf_k=60,
            )
        if self.method == "coastline":
            return CoastlineSimilarity(buffer_km=self.buffer_km)
        if self.method == "combined_rainfall":
            return CombinedSimilarity(
                alpha=0.10,
                rule_weight=0.40,
                feature_weights=OPTIMIZED_FEATURE_WEIGHTS,
                dtw_weights=OPTIMIZED_DTW_WEIGHTS,
                pool_size_factor=10,
                rrf_k=30,
                use_rainfall=True,
                rainfall_weight=0.15,
                buffer_km=self.buffer_km,
            )
        if self.method == "knn_optimized":
            return KNNSimilarity(feature_weights=OPTIMIZED_FEATURE_WEIGHTS)
        if self.method == "rule_based":
            return RuleBasedSimilarity(buffer_km=self.buffer_km)
        if self.method == "baseline":
            return BaselineSimilarity(seed=42)
        raise ValidationError(f"unknown typhoon method '{self.method}'")

    def _fit_similarity(self) -> None:
        needs_loader = self.method in (
            "coastline_rrf", "coastline", "combined_rainfall", "rule_based"
        )
        if needs_loader:
            self.similarity.fit(self.features, loader=self.loader)
        else:
            self.similarity.fit(self.features)

    # -- prediction --------------------------------------------------------
    def predict_track(
        self,
        track_df: pd.DataFrame,
        *,
        k: int = 5,
        use_rainfall: bool = False,
        rainfall_region: str = "tn",
        rainfall_weight: float | None = None,
        expected_rainfall: float | None = None,
    ) -> AnalogPrediction:
        """Find the k closest historical typhoons and vote on a track category."""
        if use_rainfall and rainfall_region not in RAINFALL_REGIONS:
            raise ValidationError(
                f"unknown rainfall region '{rainfall_region}'",
                details={"available": region_codes()},
            )

        query_features = self.extractor.extract(typhoon_id="query", track=track_df)
        query_vec = query_features.to_feature_vector()

        if self.method == "coastline":
            similar = self.similarity.find_similar_by_track(
                track_df, k=k, buffer_km=self.buffer_km
            )
            #  Vote on normalised distance: raw kilometres would underflow exp(-d).
            vote_distances = [1.0 - s for s in similar.scores]
        elif self.method == "coastline_rrf":
            self.similarity.configure_rainfall(
                use_rainfall=use_rainfall,
                region=rainfall_region,
                weight=rainfall_weight if rainfall_weight is not None else 0.08,
            )
            kwargs: dict[str, Any] = {"k": k, "buffer_km": self.buffer_km}
            if use_rainfall and expected_rainfall is not None:
                kwargs["query_rainfall"] = expected_rainfall
            similar = self.similarity.find_similar_by_track(track_df, query_vec, **kwargs)
            vote_distances = similar.distances
        elif self.method == "baseline":
            #  The random lower bound has no notion of a query vector; it draws
            #  k ids at random. "query" is not in the pool, so nothing is excluded.
            similar = self.similarity.find_similar("query", k, exclude_self=True)
            vote_distances = similar.distances
        elif self.method == "rule_based":
            rule = classify_typhoon_by_rules(
                track_df, landfall_location=None, buffer_km=self.buffer_km
            )
            knn = KNNSimilarity()
            knn.fit(self.features)
            similar = knn.find_similar_by_vector(query_vec, k=k)
            return self._compose(
                track_df,
                similar,
                predicted=rule["predicted_category"],
                confidence=float(rule.get("confidence", 0.0)),
                votes={str(rule["predicted_category"]): 1.0},
            )
        else:
            kwargs = {"k": k}
            if self.method == "combined_rainfall":
                kwargs["query_features"] = query_features
                self.similarity.configure_rainfall(
                    use_rainfall=use_rainfall,
                    region=rainfall_region,
                    weight=rainfall_weight if rainfall_weight is not None else 0.15,
                )
                if use_rainfall and expected_rainfall is not None:
                    kwargs["query_rainfall"] = expected_rainfall
            similar = self.similarity.find_similar_by_vector(query_vec, **kwargs)
            vote_distances = similar.distances

        prediction = self.model.predict(
            query_id="query",
            similar_ids=similar.similar_ids,
            distances=vote_distances,
        )
        return self._compose(
            track_df,
            similar,
            predicted=prediction.get("predicted_category"),
            confidence=float(prediction.get("confidence", 0.0)),
            votes=prediction.get("category_votes", {}),
        )

    def predict_existing(self, typhoon_id: str, **kwargs) -> AnalogPrediction:
        """Run the same analysis for a typhoon already in the dataset."""
        record = self.loader.get(typhoon_id)
        return self.predict_track(record.track, **kwargs)

    # -- validation --------------------------------------------------------
    def backtest(
        self,
        *,
        k: int = 5,
        limit: int | None = None,
        categories: list[str] | None = None,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Leave-one-out validation over the historical record.

        For every typhoon in the sample, the method is asked for its analogs
        with that typhoon itself excluded, and the analog vote is compared with
        the CWA category the forecasters actually assigned. This is the honest
        way to score an analog model: the answer is never in its own candidate
        pool.
        """
        valid = categories or VALID_CATEGORIES
        candidates = [
            tid for tid in self.features
            if self.label_dict.get(tid) in valid
        ]
        if limit and limit < len(candidates):
            #  A deterministic sample keeps repeat runs comparable.
            candidates = sorted(
                random.Random(seed).sample(candidates, limit),
                key=lambda tid: self.loader.get(tid).year,
            )

        predictions: list[dict[str, Any]] = []
        for tid in candidates:
            record = self.loader.get(tid)
            try:
                similar = self.similarity.find_similar(tid, k=k, exclude_self=True)
            except Exception as exc:  # one unusable track must not stop the run
                predictions.append(
                    {
                        "typhoon_id": tid,
                        "year": record.year,
                        "name_en": record.name_en,
                        "true_category": record.taiwan_track_category,
                        "predicted_category": None,
                        "confidence": 0.0,
                        "is_correct": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            distances = (
                [1.0 - score for score in similar.scores]
                if self.method == "coastline"
                else similar.distances
            )
            outcome = self.model.predict(
                query_id=tid, similar_ids=similar.similar_ids, distances=distances
            )
            predictions.append(
                {
                    "typhoon_id": tid,
                    "year": record.year,
                    "name_zh": record.name_zh,
                    "name_en": record.name_en,
                    "true_category": record.taiwan_track_category,
                    "predicted_category": outcome.get("predicted_category"),
                    "confidence": round(float(outcome.get("confidence", 0.0)), 4),
                    "is_correct": outcome.get("is_correct"),
                    "top_analog": similar.similar_ids[0] if similar.similar_ids else None,
                    "error": None,
                }
            )

        evaluation = compute_category_accuracy(predictions, valid_categories=valid)
        return {
            "method": self.method,
            "k": k,
            "buffer_km": self.buffer_km,
            "sample_size": len(predictions),
            "accuracy": round(evaluation.overall_score, 4),
            "correct": evaluation.correct,
            "total": evaluation.total,
            "per_category": {
                category: {
                    "total": stats["total"],
                    "correct": stats["correct"],
                    "accuracy": round(stats["accuracy"], 4),
                }
                for category, stats in sorted(evaluation.per_category.items())
            },
            "confusion": [
                {"true_category": true, "predicted_category": predicted, "count": count}
                for (true, predicted), count in sorted(evaluation.confusion_data.items())
            ],
            "predictions": predictions,
        }

    # -- presentation ------------------------------------------------------
    def _compose(
        self,
        track_df: pd.DataFrame,
        similar,
        *,
        predicted: str | None,
        confidence: float,
        votes: dict[str, float],
    ) -> AnalogPrediction:
        """Report every analog with the same absolute, interpretable distance.

        Ranking stays with each method, but the distance shown is always the
        mean path offset in km, so the top hit is never a meaningless 0 km/100%.
        """
        analogs = []
        for tid in similar.similar_ids:
            record = self.loader.get(tid)
            offset = path_offset_km(
                track_df["longitude"].values,
                track_df["latitude"].values,
                record.track["longitude"].values,
                record.track["latitude"].values,
                self.buffer_km,
            )
            score = float(np.exp(-offset / SCORE_TAU_KM)) if np.isfinite(offset) else 0.0
            analogs.append(
                {
                    "typhoon_id": tid,
                    "name_zh": record.name_zh,
                    "name_en": record.name_en,
                    "year": record.year,
                    "category": record.taiwan_track_category or "?",
                    "category_label": TRACK_CATEGORY_DESCRIPTION.get(
                        record.taiwan_track_category, ""
                    ),
                    "offset_km": round(float(offset), 1),
                    "score": round(score, 4),
                    "landfall_location": record.landfall_location,
                    "event_rain": record.event_rain,
                    "track": track_coords(record.track, self.buffer_km),
                }
            )

        return AnalogPrediction(
            method=self.method,
            predicted_category=str(predicted) if predicted is not None else None,
            confidence=round(confidence, 4),
            category_votes={str(k): round(float(v), 4) for k, v in votes.items()},
            analogs=analogs,
            buffer_km=self.buffer_km,
            rainfall=rainfall_summary(analogs),
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def track_coords(track: pd.DataFrame, buffer_km: float) -> list[dict[str, Any]]:
    """Track points with a flag for whether each fell inside the buffer."""
    lons = track["longitude"].values.astype(float)
    lats = track["latitude"].values.astype(float)
    mask = coast.clip_mask(lons, lats, buffer_km)
    return [
        {"lat": round(float(la), 4), "lon": round(float(lo), 4), "in_range": bool(m)}
        for lo, la, m in zip(lons, lats, mask, strict=True)
    ]


def rainfall_summary(analogs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Event-rainfall statistics across the analogs, per configured region."""
    stations: dict[str, Any] = {}
    for code in region_codes():
        values = [
            a["event_rain"].get(code)
            for a in analogs
            if a.get("event_rain") and a["event_rain"].get(code) is not None
        ]
        if not values:
            continue
        stations[code] = {
            "region": code,
            "label": region_label(code),
            "mean": round(float(np.mean(values)), 1),
            "median": round(float(np.median(values)), 1),
            "min": round(float(min(values)), 1),
            "max": round(float(max(values)), 1),
            "count": len(values),
        }
    return {"stations": stations} if stations else None


def _validated_buffer(buffer_km: float) -> float:
    value = float(buffer_km)
    if not MIN_BUFFER_KM <= value <= MAX_BUFFER_KM:
        raise ValidationError(
            f"buffer_km must be between {MIN_BUFFER_KM:.0f} and {MAX_BUFFER_KM:.0f}"
        )
    return value


def track_dataframe(points: list[dict]) -> pd.DataFrame:
    """Normalise an incoming track payload into the frame the algorithms expect."""
    if not isinstance(points, list) or len(points) < 2:
        raise ValidationError("a track needs at least 2 points")
    rows = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValidationError(f"track[{index}] must be an object")
        try:
            latitude = float(point["latitude"])
            longitude = float(point["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(
                f"track[{index}] needs numeric 'latitude' and 'longitude'"
            ) from exc
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 360:
            raise ValidationError(f"track[{index}] has coordinates out of range")
        rows.append(
            {
                "latitude": latitude,
                "longitude": longitude,
                "wind_kt": _optional_float(point.get("wind_kt")),
                "pressure_mb": _optional_float(point.get("pressure_mb")),
                "timestamp_utc": point.get("timestamp_utc"),
            }
        )
    frame = pd.DataFrame(rows)
    if frame["timestamp_utc"].isna().all():
        frame["timestamp_utc"] = pd.date_range(
            "2000-01-01", periods=len(frame), freq="6h"
        )
    else:
        frame["timestamp_utc"] = pd.to_datetime(
            frame["timestamp_utc"], errors="coerce", utc=True
        )
    return frame


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# engine cache
# --------------------------------------------------------------------------
_ENGINES: dict[tuple[str, float, str], TyphoonEngine] = {}
_LOCK = threading.Lock()


def get_engine(
    method: str = DEFAULT_METHOD,
    buffer_km: float = DEFAULT_BUFFER_KM,
    records: list[dict] | None = None,
    fingerprint: str | None = None,
):
    """Fitted engine for this (method, buffer, data) triple, built once.

    The data is part of the key. Fitting is expensive, so engines are cached -
    but caching on method alone would mean that changing the historical record
    and re-running gave you the answer from the previous one, which is exactly
    the thing being able to swap the dataset is for.
    """
    key = (method, _validated_buffer(buffer_km), fingerprint or "bundled")
    engine = _ENGINES.get(key)
    if engine is not None:
        return engine
    with _LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = TyphoonEngine(method=method, buffer_km=buffer_km).fit(records)
            _ENGINES[key] = engine
    return engine


def clear_engines() -> None:
    with _LOCK:
        _ENGINES.clear()


def coastline_geometry(buffer_km: float = DEFAULT_BUFFER_KM) -> dict[str, Any]:
    """Taiwan's coastline outline and the buffer polygon, for map rendering."""
    value = _validated_buffer(buffer_km)
    return {
        "buffer_km": round(value, 1),
        "coastline": coast.outline_lonlat(),
        "buffer": coast.buffer_polygon(value),
    }


def category_catalogue() -> list[dict[str, str]]:
    return [
        {"category": code, "description": text}
        for code, text in TRACK_CATEGORY_DESCRIPTION.items()
    ]
