"""
預測模型介面
"""

from abc import ABC, abstractmethod
from typing import Any


class ModelBase(ABC):
    """預測模型的抽象基類"""

    @abstractmethod
    def predict(
        self, query_id: str, similar_ids: list[str], distances: list[float], **kwargs
    ) -> dict[str, Any]:
        pass
