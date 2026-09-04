"""Result domain: a persisted, first-class execution output.

The payload shapes a plugin returns live in `app/shared/payloads.py`, and
are re-exported here so the module still reads as one vocabulary. What is
defined here is the persisted Result itself - the thing with an id, a
storage location and a place in an execution's lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.shared.ids import new_id, utcnow
from app.shared.payloads import (  # re-exported: one vocabulary, one import
    INLINE_PAYLOAD_MAX_BYTES,
    ResultKind,
    ResultPayload,
)

__all__ = ["INLINE_PAYLOAD_MAX_BYTES", "Result", "ResultKind", "ResultPayload"]


@dataclass
class Result:
    """A persisted, first-class execution output."""

    execution_id: str
    kind: ResultKind
    summary: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    inline_payload: Any = None
    payload_uri: str | None = None
    artifact_uri: str | None = None
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    row_count: int | None = None
    id: str = field(default_factory=lambda: new_id("res"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def is_materialised(self) -> bool:
        return self.dataset_version_id is not None
