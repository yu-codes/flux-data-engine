"""Analysis domain: exploring data and saving what you found.

Visualisations bind to a dataset version or a result - never to a file format.
By the time anything reaches this module the data is already a normalised
Dataset, so charts never learn that CSV or Excel exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.ids import new_id, utcnow


class ChartType(str, Enum):
    LINE = "line"
    BAR = "bar"
    STACKED_BAR = "stacked_bar"
    AREA = "area"
    SCATTER = "scatter"
    PIE = "pie"
    HISTOGRAM = "histogram"
    BOX = "box"
    HEATMAP = "heatmap"
    TABLE = "table"
    METRIC = "metric"

    @property
    def needs_distribution(self) -> bool:
        """Charts built from the shape of a column, not from row-by-row values."""
        return self in (ChartType.HISTOGRAM, ChartType.BOX)

    @property
    def needs_grid(self) -> bool:
        """Charts that read two categorical axes against one measure."""
        return self is ChartType.HEATMAP


class Aggregation(str, Enum):
    NONE = "none"
    SUM = "sum"
    MEAN = "mean"
    MIN = "min"
    MAX = "max"
    COUNT = "count"


@dataclass
class ChartSpec:
    """What to draw, and how to label it.

    The presentation fields matter as much as the data ones: a chart without
    axis titles and units is a picture of numbers rather than a reading of
    them. Anything left unset is derived from the column names at render time,
    so a chart is never unlabelled.
    """

    chart_type: ChartType = ChartType.BAR
    x: str | None = None
    y: list[str] = field(default_factory=list)
    series: str | None = None
    aggregation: Aggregation = Aggregation.NONE
    limit: int = 500
    sort_by: str | None = None
    sort_desc: bool = False
    #  Histogram only: how many equal-width buckets to cut the column into.
    bins: int = 12
    #  Ordinal categories are not alphabetical: an intensity scale reads
    #  mild → moderate → severe, whatever those words sort as. Anything not
    #  named keeps its natural order after the named ones.
    x_order: list[str] = field(default_factory=list)
    series_order: list[str] = field(default_factory=list)
    # -- presentation ------------------------------------------------------
    x_title: str = ""
    y_title: str = ""
    unit: str = ""
    subtitle: str = ""
    value_labels: bool = False
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chart_type": self.chart_type.value,
            "x": self.x,
            "y": self.y,
            "series": self.series,
            "aggregation": self.aggregation.value,
            "limit": self.limit,
            "sort_by": self.sort_by,
            "sort_desc": self.sort_desc,
            "bins": self.bins,
            "x_order": self.x_order,
            "series_order": self.series_order,
            "x_title": self.x_title,
            "y_title": self.y_title,
            "unit": self.unit,
            "subtitle": self.subtitle,
            "value_labels": self.value_labels,
            "options": self.options,
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> ChartSpec:
        raw = raw or {}
        return cls(
            chart_type=ChartType(raw.get("chart_type", "bar")),
            x=raw.get("x"),
            y=list(raw.get("y") or []),
            series=raw.get("series"),
            aggregation=Aggregation(raw.get("aggregation", "none")),
            limit=int(raw.get("limit", 500)),
            sort_by=raw.get("sort_by"),
            sort_desc=bool(raw.get("sort_desc", False)),
            bins=max(2, min(int(raw.get("bins", 12) or 12), 60)),
            x_order=[str(v) for v in (raw.get("x_order") or [])],
            series_order=[str(v) for v in (raw.get("series_order") or [])],
            x_title=raw.get("x_title", "") or "",
            y_title=raw.get("y_title", "") or "",
            unit=raw.get("unit", "") or "",
            subtitle=raw.get("subtitle", "") or "",
            value_labels=bool(raw.get("value_labels", False)),
            options=raw.get("options") or {},
        )

    def resolved_x_title(self) -> str:
        if self.x_title:
            return self.x_title
        if self.chart_type is ChartType.HISTOGRAM and self.y:
            return self.y[0]
        return self.x or ""

    def resolved_y_title(self) -> str:
        """Falls back to "<aggregation> of <column>", which is what it is."""
        if self.y_title:
            return self.y_title
        if not self.y:
            return ""
        columns = ", ".join(self.y)
        if self.chart_type is ChartType.HISTOGRAM:
            return "rows"
        if self.chart_type is ChartType.BOX or self.aggregation is Aggregation.NONE:
            return columns
        return f"{self.aggregation.value} of {columns}"


@dataclass
class Visualization:
    name: str
    spec: ChartSpec = field(default_factory=ChartSpec)
    dataset_id: str | None = None
    dataset_version_id: str | None = None
    result_id: str | None = None
    description: str = ""
    id: str = field(default_factory=lambda: new_id("viz"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class DashboardTile:
    visualization_id: str
    x: int = 0
    y: int = 0
    width: int = 6
    height: int = 4

    def to_dict(self) -> dict:
        return {
            "visualization_id": self.visualization_id,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> DashboardTile:
        return cls(
            visualization_id=raw["visualization_id"],
            x=int(raw.get("x", 0)),
            y=int(raw.get("y", 0)),
            width=int(raw.get("width", 6)),
            height=int(raw.get("height", 4)),
        )


@dataclass
class Dashboard:
    name: str
    description: str = ""
    tiles: list[DashboardTile] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("dash"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)
