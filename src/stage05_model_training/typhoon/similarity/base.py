"""
相似度計算介面
"""

from abc import ABC, abstractmethod
import numpy as np
from dataclasses import dataclass


@dataclass
class SimilarityResult:
    """相似度查詢結果"""

    query_id: str
    similar_ids: list[str]
    distances: list[float]
    scores: list[float]  # 1 - normalized_distance（越大越相似）


class SimilarityBase(ABC):
    """相似度計算的抽象基類"""

    @abstractmethod
    def fit(self, feature_dict: dict):
        """
        擬合參考資料

        Args:
            feature_dict: {typhoon_id: TyphoonFeatures}
        """
        pass

    @abstractmethod
    def find_similar(
        self, query_id: str, k: int = 5, exclude_self: bool = True
    ) -> SimilarityResult:
        """
        找最相似的 K 個颱風

        Args:
            query_id: 查詢颱風 ID
            k: 返回數量
            exclude_self: 是否排除自己

        Returns:
            SimilarityResult
        """
        pass

    @abstractmethod
    def compute_distance(self, id_a: str, id_b: str) -> float:
        """計算兩個颱風之間的距離"""
        pass
