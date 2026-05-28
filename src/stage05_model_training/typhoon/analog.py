"""
類比預測模型

用途：基於相似颱風的「侵臺路徑分類」做加權投票預測
"""

import numpy as np
from collections import Counter
from typing import Any
from .model_base import ModelBase


class AnalogModel(ModelBase):
    """
    類比預測模型

    以相似颱風的歷史紀錄做加權投票 / 平均
    """

    def __init__(self, label_dict: dict[str, str] | None = None):
        """
        Args:
            label_dict: {typhoon_id: taiwan_track_category} — 真實標籤
        """
        self._label_dict = label_dict or {}

    def set_labels(self, label_dict: dict[str, str]):
        self._label_dict = label_dict

    def predict(
        self, query_id: str, similar_ids: list[str], distances: list[float], **kwargs
    ) -> dict[str, Any]:
        """
        預測侵臺路徑分類

        策略：距離倒數加權投票

        Returns:
            {
                "predicted_category": str,
                "confidence": float,
                "category_votes": dict,
                "analogs": list[dict],
                "true_category": str | None,
                "is_correct": bool | None,
            }
        """
        if not similar_ids:
            return {
                "predicted_category": None,
                "confidence": 0.0,
                "error": "No analogs",
            }

        # 取得各相似颱風的真實分類
        analogs = []
        categories = []
        weights_list = []

        for tid, dist in zip(similar_ids, distances):
            cat = self._label_dict.get(tid)
            if cat is None:
                continue
            weight = float(np.exp(-dist))
            analogs.append(
                {"typhoon_id": tid, "distance": dist, "category": cat, "weight": weight}
            )
            categories.append(cat)
            weights_list.append(weight)

        if not categories:
            return {
                "predicted_category": None,
                "confidence": 0.0,
                "error": "No labeled analogs",
            }

        # 加權投票
        vote_weights = {}
        for cat, w in zip(categories, weights_list):
            vote_weights[cat] = vote_weights.get(cat, 0.0) + w

        total_weight = sum(vote_weights.values())
        vote_probs = {cat: w / total_weight for cat, w in vote_weights.items()}

        predicted = max(vote_probs, key=vote_probs.get)
        confidence = vote_probs[predicted]

        # 真實標籤
        true_cat = self._label_dict.get(query_id)
        is_correct = (true_cat == predicted) if true_cat is not None else None

        return {
            "predicted_category": predicted,
            "confidence": confidence,
            "category_votes": vote_probs,
            "analogs": analogs,
            "true_category": true_cat,
            "is_correct": is_correct,
        }
