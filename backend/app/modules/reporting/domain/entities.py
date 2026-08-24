"""Reports: composed, exportable narratives over results.

A Report holds an ordered list of sections. Sections do not copy data — they
reference it, so a report re-rendered tomorrow shows tomorrow's numbers unless
it has been explicitly frozen by exporting a snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.ids import new_id, utcnow


class SectionKind(str, Enum):
    TEXT = "text"                  # markdown prose
    METRICS = "metrics"            # an execution's metrics as a table
    TABLE = "table"                # rows from a result or dataset version
    CHART = "chart"                # a saved visualization
    EXECUTION = "execution"        # an execution's provenance summary
    RESULT = "result"              # a result payload, whatever its shape
    MODEL = "model"                # a model's contracts and versions


class ReportStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class ExportFormat(str, Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"


@dataclass
class ReportSection:
    kind: SectionKind
    title: str = ""
    body: str = ""
    execution_id: str | None = None
    result_id: str | None = None
    dataset_version_id: str | None = None
    visualization_id: str | None = None
    model_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "title": self.title,
            "body": self.body,
            "execution_id": self.execution_id,
            "result_id": self.result_id,
            "dataset_version_id": self.dataset_version_id,
            "visualization_id": self.visualization_id,
            "model_id": self.model_id,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> ReportSection:
        return cls(
            kind=SectionKind(raw["kind"]),
            title=raw.get("title", "") or "",
            body=raw.get("body", "") or "",
            execution_id=raw.get("execution_id"),
            result_id=raw.get("result_id"),
            dataset_version_id=raw.get("dataset_version_id"),
            visualization_id=raw.get("visualization_id"),
            model_id=raw.get("model_id"),
            options=raw.get("options") or {},
        )

    def reference(self) -> str | None:
        """The id this section reads from, whichever kind it is."""
        return (
            self.result_id
            or self.execution_id
            or self.dataset_version_id
            or self.visualization_id
            or self.model_id
        )


@dataclass
class Report:
    name: str
    description: str = ""
    sections: list[ReportSection] = field(default_factory=list)
    status: ReportStatus = ReportStatus.DRAFT
    tags: list[str] = field(default_factory=list)
    #  Set when the report was last exported to a file in the object store.
    last_export_uri: str | None = None
    last_export_format: str | None = None
    last_exported_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("rep"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)
