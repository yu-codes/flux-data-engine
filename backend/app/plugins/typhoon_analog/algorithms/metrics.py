"""
可插拔的評估指標模組

支援：
- category_accuracy: 路徑分類準確率 (LOO)
- 未來可擴充：precipitation_loss, track_rmse 等
"""

from typing import Any
from dataclasses import dataclass


@dataclass
class EvaluationResult:
    """評估結果容器"""

    metric_name: str
    overall_score: float
    total: int
    correct: int
    per_category: dict[str, dict]
    confusion_data: dict[tuple, int]
    details: list[dict] | None = None


def compute_category_accuracy(
    predictions: list[dict],
    valid_categories: list[str] | None = None,
) -> EvaluationResult:
    """
    計算路徑分類準確率

    Args:
        predictions: list of {typhoon_id, true_category, predicted_category, ...}
        valid_categories: 只評估這些分類 (None = 全部)

    Returns:
        EvaluationResult
    """
    correct = 0
    total = 0
    per_category: dict[str, dict] = {}
    confusion: dict[tuple, int] = {}

    for pred in predictions:
        true_cat = pred.get("true_category")
        pred_cat = pred.get("predicted_category")

        if not true_cat or not pred_cat:
            continue

        # Filter by valid categories
        if valid_categories and true_cat not in valid_categories:
            continue

        total += 1
        is_correct = true_cat == pred_cat
        if is_correct:
            correct += 1

        # Per-category stats
        if true_cat not in per_category:
            per_category[true_cat] = {"total": 0, "correct": 0}
        per_category[true_cat]["total"] += 1
        if is_correct:
            per_category[true_cat]["correct"] += 1

        # Confusion matrix
        key = (true_cat, pred_cat)
        confusion[key] = confusion.get(key, 0) + 1

    # Compute accuracy per category
    for cat_info in per_category.values():
        cat_info["accuracy"] = (
            cat_info["correct"] / cat_info["total"] if cat_info["total"] > 0 else 0.0
        )

    overall = correct / total if total > 0 else 0.0

    return EvaluationResult(
        metric_name="category_accuracy",
        overall_score=overall,
        total=total,
        correct=correct,
        per_category=per_category,
        confusion_data=confusion,
    )


# Registry of available metrics
METRIC_REGISTRY: dict[str, Any] = {
    "category_accuracy": compute_category_accuracy,
}
