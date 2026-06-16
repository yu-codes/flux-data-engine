"""
Stage 06 — 超參數優化共用工具

提供資料載入、中文字型設定等共用函式。
"""

import sys
import json
import csv
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

ROOT_DIR = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from data_pipiline.stage00_data_ingestion.typhoon.loader import DataLoader
from data_pipiline.stage04_feature_engineering.typhoon.extractor import (
    TyphoonFeatureExtractor,
    TyphoonFeatures,
)
from data_pipiline.stage05_model_training.typhoon.similarity.knn import KNNSimilarity
from data_pipiline.stage05_model_training.typhoon.similarity.dtw import DTWSimilarity
from data_pipiline.stage05_model_training.typhoon.similarity.combined import (
    CombinedSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.similarity.rule_based import (
    classify_typhoon_by_rules,
    RuleBasedSimilarity,
)
from data_pipiline.stage05_model_training.typhoon.analog import AnalogModel
from data_pipiline.stage05_model_training.typhoon.mapping import ImpactMapper

VALID_CATEGORIES = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
OUTPUT_DIR = ROOT_DIR / "experiments" / "typhoon" / "analysis" / "methods"


def setup_chinese_font():
    candidates = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei", "PingFang TC"]
    for font_name in candidates:
        try:
            fm.findfont(font_name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [font_name]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


def load_data():
    """載入資料集並提取特徵"""
    loader = DataLoader(str(ROOT_DIR / "data" / "typhoon" / "preprocessed"))
    loader.load()

    extractor = TyphoonFeatureExtractor(impact_radius_km=500.0)
    features = extractor.extract_all(loader)
    label_dict = ImpactMapper.build_label_dict(loader)

    # 載入降水資料
    rainfall = {}
    rain_csv = (
        ROOT_DIR / "data" / "raw" / "typhoon_events_rainfall" / "颱風事件雨量.csv"
    )
    if rain_csv.exists():
        with open(rain_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tid = row.get("typhoon_id", "").strip()
                if tid:
                    try:
                        rainfall[tid] = {
                            "tainan": float(row.get("臺南", 0) or 0),
                            "kaohsiung": float(row.get("高雄", 0) or 0),
                        }
                    except (ValueError, TypeError):
                        pass

    return loader, features, label_dict, rainfall
