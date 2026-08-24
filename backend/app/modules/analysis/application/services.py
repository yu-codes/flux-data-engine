"""Analysis services: explore datasets, save charts, assemble dashboards."""

from __future__ import annotations

from typing import Any

from app.modules.data.application.services import DatasetService, profile_table
from app.modules.results.application.services import ResultService
from app.shared.contracts import FieldSpec
from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.tabular import Table

from ..domain.entities import (
    Aggregation,
    ChartSpec,
    ChartType,
    Dashboard,
    DashboardTile,
    Visualization,
)
from ..domain.ports import DashboardRepository, VisualizationRepository

MAX_QUERY_ROWS = 5000


class ExploreService:
    """Read-only slicing, filtering and aggregation over dataset versions."""

    def __init__(self, datasets: DatasetService):
        self.datasets = datasets

    def profile(self, version_id: str) -> dict[str, Any]:
        version = self.datasets.get_version(version_id)
        table = self.datasets.read_table(version_id)
        return {
            "version_id": version.id,
            "row_count": version.row_count,
            "column_count": version.column_count,
            "columns": profile_table(table),
        }

    def query(
        self,
        version_id: str,
        *,
        columns: list[str] | None = None,
        filters: list[dict] | None = None,
        sort_by: str | None = None,
        sort_desc: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        #  The full schema is reported whatever the projection, because the
        #  column picker needs to offer the columns that were not selected.
        schema = self.datasets.schema_fields(version_id)

        #  Filtering and sorting happen in Arrow; only the page that is about
        #  to be returned is turned into dicts. Before this, the whole dataset
        #  became a list of dicts first and `limit` merely trimmed the reply -
        #  so the pagination controls were decorative and the cost of asking
        #  for twenty rows was the cost of reading everything.
        table = self.datasets.read_table(version_id, columns=self._needed(
            columns, filters, sort_by, schema
        ))
        if filters:
            table = table.filter(filters)
        if sort_by:
            table = table.sort(sort_by, sort_desc)

        total = table.num_rows
        limit = max(1, min(int(limit), MAX_QUERY_ROWS))
        page = table.slice(offset, limit)
        if columns:
            page = page.select(columns)
        return {
            "rows": page.to_rows(),
            "total": total,
            "offset": offset,
            "limit": limit,
            "columns": [f.to_dict() for f in schema],
        }

    @staticmethod
    def _needed(
        columns: list[str] | None,
        filters: list[dict] | None,
        sort_by: str | None,
        schema: list[FieldSpec],
    ) -> list[str] | None:
        """Which columns have to be read to answer this query.

        None means all of them - the honest answer when no projection was
        asked for, since the caller wants every column back.
        """
        if not columns:
            return None
        wanted = list(columns)
        wanted.extend(f["column"] for f in (filters or []) if f.get("column"))
        if sort_by:
            wanted.append(sort_by)
        known = {f.name for f in schema}
        return [c for c in dict.fromkeys(wanted) if c in known] or None

    def series(self, version_id: str, spec: ChartSpec) -> dict[str, Any]:
        """Draw a chart, reading only the columns it names.

        The narrowing used to happen after the file was read, which meant a
        two-column chart over a forty-column dataset still paid for forty
        columns of Parquet. Pushed into the read, it pays for two.
        """
        wanted = _chart_columns(spec, self.datasets.schema_fields(version_id))
        table = self.datasets.read_table(version_id, columns=wanted)
        return build_series(table, spec)


def _chart_columns(spec: ChartSpec, schema: list[FieldSpec]) -> list[str] | None:
    """The columns a chart reads, or None when that cannot be narrowed.

    A name the dataset does not have is left out rather than passed to the
    reader, which would fail the whole read; `build_series` still refuses the
    chart, with a message naming the column.
    """
    wanted = [c for c in (spec.x, spec.series, spec.sort_by) if c]
    wanted.extend(spec.y)
    known = {f.name for f in schema}
    present = [c for c in dict.fromkeys(wanted) if c in known]
    return present or None


def build_series(table: Table, spec: ChartSpec) -> dict[str, Any]:
    """Turn a table plus a chart spec into plot-ready categories and series.

    Two things happen before any Python touches a value. The table is narrowed
    to the columns this chart actually names - a chart of two columns from a
    forty-column table should read two columns - and ordering, grouping and
    aggregation are done in Arrow. Only the shapes that are genuinely
    row-wise, like Tukey whiskers, materialise rows, and by then they are
    reading a handful of columns rather than the whole table.
    """
    if not spec.y:
        raise ValidationError("the chart spec needs at least one y column")

    total = table.num_rows
    table = _narrowed(table, spec)
    if spec.sort_by and spec.sort_by in table.columns:
        table = table.sort(spec.sort_by, spec.sort_desc)

    #  Distribution and grid charts read a column differently from a plain
    #  series, so they get their own builders rather than a flag in this one.
    if spec.chart_type is ChartType.HISTOGRAM:
        return _histogram(table, spec)
    if spec.chart_type is ChartType.BOX:
        return _boxes(table, spec)
    if spec.chart_type is ChartType.HEATMAP:
        return _heatmap(table, spec)
    if spec.series:
        return _split_by_series(table, spec)

    if spec.aggregation is Aggregation.NONE:
        #  Only the rows that will be plotted are ever built.
        page = table.slice(0, spec.limit)
        categories = page.column_values(spec.x) if spec.x else list(range(page.num_rows))
        series = [
            {"name": column, "data": [_numeric(v) for v in page.column_values(column)]}
            for column in spec.y
        ]
        return _chart(spec, categories, series, page.num_rows)

    if not spec.x:
        raise ValidationError("aggregation needs an x column to group by")
    return _aggregated(table, spec, total)


def _narrowed(table: Table, spec: ChartSpec) -> Table:
    """Keep only the columns this chart names.

    A chart that cannot find a column it needs is a validation error rather
    than a silent empty plot, so a missing name is left in place for the
    builder below to complain about.
    """
    wanted = [c for c in (spec.x, spec.series, spec.sort_by) if c]
    wanted.extend(spec.y)
    present = [c for c in dict.fromkeys(wanted) if c in table.columns]
    return table.select(present) if present else table


def _aggregated(table: Table, spec: ChartSpec, total: int) -> dict[str, Any]:
    """Group in Arrow, then assemble one series per y column."""
    how = spec.aggregation.value
    if spec.x not in table.columns:
        raise ValidationError(
            f"no column '{spec.x}' to group by", details={"columns": table.columns}
        )
    grouped = table.group_aggregate(spec.x, spec.y, how).to_rows()
    by_key = {row[spec.x]: row for row in grouped}

    ordered = _ordered_categories(by_key, spec)[: spec.limit]
    series = []
    for column in spec.y:
        key = "count" if how == "count" else f"{column}_{how}"
        data = [_rounded(by_key[category].get(key)) for category in ordered]
        series.append({"name": f"{column} ({how})", "data": data})
    return _chart(spec, ordered, series, total)


def _rounded(value: Any) -> float | None:
    """Aggregates are reported to six places, as they always were."""
    if value is None:
        return None
    number = float(value)
    return round(number, 6)


def _histogram(table: Table, spec: ChartSpec) -> dict[str, Any]:
    """Equal-width buckets over one numeric column, counted.

    The shape of a variable is a different question from its value per row, and
    it is the question a distribution answers: how often, and how spread out.
    """
    column = spec.y[0]
    #  One column, straight out of Arrow as floats - a histogram never needed
    #  the rest of the table and never needed a dict per row.
    values = table.numeric_values(column)
    if not values:
        return _chart(spec, [], [], table.num_rows)

    low, high = min(values), max(values)
    if low == high:
        label = _bucket_label(low, high)
        series = [{"name": f"{column} (count)", "data": [float(len(values))]}]
        return _chart(spec, [label], series, len(values))

    bins = spec.bins
    width = (high - low) / bins
    counts = [0] * bins
    for value in values:
        #  The top edge belongs to the last bucket rather than to a bucket of
        #  its own: the usual convention for a closed final interval.
        index = min(bins - 1, int((value - low) / width))
        counts[index] += 1

    edges = [low + width * i for i in range(bins + 1)]
    categories = [_bucket_label(edges[i], edges[i + 1]) for i in range(bins)]
    series = [{"name": f"{column} (count)", "data": [float(c) for c in counts]}]
    payload = _chart(spec, categories, series, len(values))
    ordered = sorted(values)
    payload["distribution"] = {
        "column": column,
        "min": low,
        "max": high,
        "mean": sum(values) / len(values),
        "median": _quantile(ordered, 0.5),
        "p90": _quantile(ordered, 0.9),
        "bins": bins,
        "counted": len(values),
    }
    return payload


def _boxes(table: Table, spec: ChartSpec) -> dict[str, Any]:
    """Five-number summary of one numeric column, per category.

    A mean hides the spread; a box shows it. For skewed measures - rainfall is
    the obvious one - the median and the quartiles say far more than the mean.
    """
    column = spec.y[0]
    total = table.num_rows
    measures = table.column_values(column)
    keys = table.column_values(spec.x) if spec.x else [column] * total
    grouped: dict[Any, list[float]] = {}
    for key, raw in zip(keys, measures, strict=True):
        value = _numeric(raw)
        if value is None:
            continue
        grouped.setdefault(key, []).append(value)

    if not grouped:
        return _chart(spec, [], [], total)

    ordered = _ordered_categories(grouped, spec)[: spec.limit]
    stats: dict[str, list[float]] = {
        name: [] for name in ("min", "q1", "median", "q3", "max")
    }
    outliers: list[dict[str, Any]] = []
    for key in ordered:
        values = sorted(grouped[key])
        q1 = _quantile(values, 0.25)
        q3 = _quantile(values, 0.75)
        #  Tukey whiskers: reach to the furthest point inside 1.5 IQR, and call
        #  anything past that an outlier rather than stretching the box.
        fence = 1.5 * (q3 - q1)
        inside = [v for v in values if q1 - fence <= v <= q3 + fence] or values
        stats["min"].append(inside[0])
        stats["q1"].append(q1)
        stats["median"].append(_quantile(values, 0.5))
        stats["q3"].append(q3)
        stats["max"].append(inside[-1])
        for value in values:
            if value < inside[0] or value > inside[-1]:
                outliers.append({"category": key, "value": value})

    series = [{"name": name, "data": values} for name, values in stats.items()]
    payload = _chart(spec, ordered, series, total)
    payload["outliers"] = outliers[:200]
    payload["group_sizes"] = [len(grouped[key]) for key in ordered]
    return payload


def _heatmap(table: Table, spec: ChartSpec) -> dict[str, Any]:
    """One measure read against two categorical axes.

    Two categories at once - month against track category, say - is where a
    grid beats a grouped bar chart: the eye finds the hot cell immediately.
    """
    if not spec.x or not spec.series:
        raise ValidationError("a heatmap needs both an x column and a series column")
    return _grid(table, spec)


def _split_by_series(table: Table, spec: ChartSpec) -> dict[str, Any]:
    """One line or bar group per distinct value of the series column.

    Without this a chart can only compare columns; with it, a chart compares
    cohorts - the same measure for each category, side by side or stacked.
    """
    if not spec.x:
        raise ValidationError("splitting by series needs an x column")
    return _grid(table, spec)


def _grid(table: Table, spec: ChartSpec) -> dict[str, Any]:
    """x by series, aggregated - the shared shape behind heatmaps and cohorts."""
    column = spec.y[0]
    total = table.num_rows
    #  Three columns, whatever the table is wide.
    xs = table.column_values(spec.x)
    bands_column = table.column_values(spec.series)
    measures = table.column_values(column)
    cells: dict[tuple[Any, Any], list[float]] = {}
    sizes: dict[tuple[Any, Any], int] = {}
    for x_value, band, raw in zip(xs, bands_column, measures, strict=True):
        key = (x_value, band)
        sizes[key] = sizes.get(key, 0) + 1
        value = _numeric(raw)
        if value is not None:
            cells.setdefault(key, []).append(value)

    x_values = _sorted_keys({key[0] for key in sizes}, spec.x_order)[: spec.limit]
    bands = _sorted_keys({key[1] for key in sizes}, spec.series_order)

    series = []
    for band in bands:
        data: list[float | None] = []
        for x_value in x_values:
            key = (x_value, band)
            if spec.aggregation is Aggregation.COUNT:
                data.append(float(sizes.get(key, 0)))
            elif spec.aggregation is Aggregation.NONE:
                values = cells.get(key, [])
                data.append(values[0] if values else None)
            else:
                values = cells.get(key, [])
                data.append(_aggregate(values, spec.aggregation) if values else None)
        series.append({"name": _band_label(band), "data": data})

    payload = _chart(spec, x_values, series, total)
    payload["band_title"] = spec.series
    return payload


def _band_label(value: Any) -> str:
    return "(none)" if value is None or value == "" else str(value)


def _sorted_keys(keys, stated: list[str] | None = None) -> list[Any]:
    """Stated order first, in the order given; everything else after it."""
    numeric = all(
        isinstance(key, (int, float)) and not isinstance(key, bool) for key in keys
    )
    natural = sorted(keys, key=lambda key: (key is None, key if numeric else str(key)))
    if not stated:
        return natural
    position = {name: index for index, name in enumerate(stated)}
    return sorted(
        natural, key=lambda key: (position.get(_band_label(key), len(position)),)
    )


def _quantile(ordered: list[float], q: float) -> float:
    """Linear interpolation between the two neighbouring order statistics."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bucket_label(low: float, high: float) -> str:
    span = high - low
    digits = 0 if span >= 10 else (1 if span >= 1 else 2)
    return f"{low:.{digits}f}-{high:.{digits}f}"


def _ordered_categories(grouped: dict[Any, Any], spec: ChartSpec) -> list[Any]:
    """Group order: the requested sort if there is one, else the key's own order.

    A category axis reads badly when the buckets arrive in whatever order the
    rows happened to be in, so unsorted groups are ordered by their key.
    """
    keys = list(grouped)
    if spec.sort_by:
        return keys
    return _sorted_keys(keys, spec.x_order)


def _chart(spec: ChartSpec, categories, series, row_count: int) -> dict[str, Any]:
    """One envelope, so every renderer gets the labels it needs."""
    return {
        "categories": categories,
        "series": series,
        "chart_type": spec.chart_type.value,
        "x_title": spec.resolved_x_title(),
        "y_title": spec.resolved_y_title(),
        "unit": spec.unit,
        "subtitle": spec.subtitle,
        "value_labels": spec.value_labels,
        "aggregation": spec.aggregation.value,
        "row_count": row_count,
    }


class VisualizationService:
    def __init__(
        self,
        repository: VisualizationRepository,
        datasets: DatasetService,
        results: ResultService,
    ):
        self.repository = repository
        self.datasets = datasets
        self.results = results

    def create(
        self,
        *,
        name: str,
        spec: dict,
        dataset_version_id: str | None = None,
        dataset_id: str | None = None,
        result_id: str | None = None,
        description: str = "",
    ) -> Visualization:
        version_id = self._resolve_version(dataset_version_id, dataset_id, result_id)
        entity = Visualization(
            name=name,
            description=description,
            spec=ChartSpec.from_dict(spec),
            dataset_id=dataset_id,
            dataset_version_id=version_id,
            result_id=result_id,
        )
        return self.repository.add(entity)

    def get(self, visualization_id: str) -> Visualization:
        entity = self.repository.get(visualization_id)
        if not entity:
            raise NotFoundError(f"visualization '{visualization_id}' not found")
        return entity

    def list(self) -> list[Visualization]:
        return self.repository.list()

    def update(self, visualization_id: str, changes: dict) -> Visualization:
        entity = self.get(visualization_id)
        if changes.get("name"):
            entity.name = changes["name"]
        if changes.get("description") is not None:
            entity.description = changes["description"]
        if changes.get("spec") is not None:
            entity.spec = ChartSpec.from_dict(changes["spec"])
        return self.repository.update(entity)

    def delete(self, visualization_id: str) -> None:
        self.repository.delete(visualization_id)

    def render(self, visualization_id: str) -> dict[str, Any]:
        entity = self.get(visualization_id)
        if not entity.dataset_version_id:
            raise ValidationError("this visualization has no bound dataset version")
        table = self.datasets.read_table(entity.dataset_version_id)
        payload = build_series(table, entity.spec)
        payload["name"] = entity.name
        payload["visualization_id"] = entity.id
        return payload

    def _resolve_version(
        self,
        dataset_version_id: str | None,
        dataset_id: str | None,
        result_id: str | None,
    ) -> str:
        if dataset_version_id:
            self.datasets.get_version(dataset_version_id)
            return dataset_version_id
        if dataset_id:
            return self.datasets.current_version(dataset_id).id
        if result_id:
            result = self.results.get(result_id)
            if not result.dataset_version_id:
                raise ValidationError(
                    "this result is not materialised as a dataset; materialise it first"
                )
            return result.dataset_version_id
        raise ValidationError(
            "provide dataset_version_id, dataset_id or result_id to bind the chart"
        )


class DashboardService:
    def __init__(
        self, repository: DashboardRepository, visualizations: VisualizationService
    ):
        self.repository = repository
        self.visualizations = visualizations

    def create(self, *, name: str, description: str = "", tiles: list[dict] | None = None):
        if self.repository.get_by_name(name):
            raise ConflictError(f"a dashboard named '{name}' already exists")
        return self.repository.add(
            Dashboard(
                name=name,
                description=description,
                tiles=[DashboardTile.from_dict(t) for t in (tiles or [])],
            )
        )

    def get(self, dashboard_id: str) -> Dashboard:
        entity = self.repository.get(dashboard_id)
        if not entity:
            raise NotFoundError(f"dashboard '{dashboard_id}' not found")
        return entity

    def list(self) -> list[Dashboard]:
        return self.repository.list()

    def update(self, dashboard_id: str, changes: dict) -> Dashboard:
        entity = self.get(dashboard_id)
        if changes.get("name"):
            entity.name = changes["name"]
        if changes.get("description") is not None:
            entity.description = changes["description"]
        if changes.get("tiles") is not None:
            entity.tiles = [DashboardTile.from_dict(t) for t in changes["tiles"]]
        return self.repository.update(entity)

    def add_tile(
        self,
        dashboard_id: str,
        *,
        visualization_id: str,
        width: int = 6,
        height: int = 4,
    ) -> Dashboard:
        """Append a chart to a dashboard that already exists.

        A dashboard is a working surface, not a fixed publication: the whole
        point is that you can keep adding to it as the analysis develops.
        """
        entity = self.get(dashboard_id)
        #  Fail here rather than rendering a broken tile later.
        self.visualizations.get(visualization_id)
        if any(tile.visualization_id == visualization_id for tile in entity.tiles):
            raise ConflictError("that chart is already on this dashboard")
        width = max(3, min(int(width), 12))
        entity.tiles.append(
            DashboardTile(
                visualization_id=visualization_id,
                width=width,
                height=max(2, min(int(height), 12)),
                **_next_slot(entity.tiles, width),
            )
        )
        return self.repository.update(entity)

    def update_tile(self, dashboard_id: str, visualization_id: str, changes: dict):
        entity = self.get(dashboard_id)
        tile = _find_tile(entity, visualization_id)
        if changes.get("width") is not None:
            tile.width = max(3, min(int(changes["width"]), 12))
        if changes.get("height") is not None:
            tile.height = max(2, min(int(changes["height"]), 12))
        return self.repository.update(entity)

    def move_tile(self, dashboard_id: str, visualization_id: str, offset: int):
        """Shift a tile earlier or later in reading order."""
        entity = self.get(dashboard_id)
        tile = _find_tile(entity, visualization_id)
        index = entity.tiles.index(tile)
        target = max(0, min(index + offset, len(entity.tiles) - 1))
        if target != index:
            entity.tiles.insert(target, entity.tiles.pop(index))
            _relayout(entity.tiles)
        return self.repository.update(entity)

    def remove_tile(self, dashboard_id: str, visualization_id: str) -> Dashboard:
        entity = self.get(dashboard_id)
        tile = _find_tile(entity, visualization_id)
        entity.tiles.remove(tile)
        _relayout(entity.tiles)
        return self.repository.update(entity)

    def delete(self, dashboard_id: str) -> None:
        self.repository.delete(dashboard_id)

    def render(self, dashboard_id: str) -> dict[str, Any]:
        dashboard = self.get(dashboard_id)
        tiles = []
        for tile in dashboard.tiles:
            try:
                chart = self.visualizations.render(tile.visualization_id)
            except (NotFoundError, ValidationError) as exc:
                chart = {"error": str(exc), "visualization_id": tile.visualization_id}
            tiles.append({**tile.to_dict(), "chart": chart})
        return {"id": dashboard.id, "name": dashboard.name,
                "description": dashboard.description, "tiles": tiles}


# --------------------------------------------------------------------------
# dashboard layout
# --------------------------------------------------------------------------
GRID_COLUMNS = 12


def _find_tile(dashboard: Dashboard, visualization_id: str) -> DashboardTile:
    tile = next(
        (t for t in dashboard.tiles if t.visualization_id == visualization_id), None
    )
    if tile is None:
        raise NotFoundError(f"chart '{visualization_id}' is not on this dashboard")
    return tile


def _next_slot(tiles: list[DashboardTile], width: int) -> dict[str, int]:
    """Place a new tile beside the last one if it fits, otherwise on a new row."""
    if not tiles:
        return {"x": 0, "y": 0}
    last = tiles[-1]
    if last.x + last.width + width <= GRID_COLUMNS:
        return {"x": last.x + last.width, "y": last.y}
    return {"x": 0, "y": last.y + last.height}


def _relayout(tiles: list[DashboardTile]) -> None:
    """Re-flow the grid so removing a tile never leaves a hole behind it."""
    x = y = row_height = 0
    for tile in tiles:
        if x + tile.width > GRID_COLUMNS:
            x = 0
            y += row_height or tile.height
            row_height = 0
        tile.x, tile.y = x, y
        x += tile.width
        row_height = max(row_height, tile.height)


# --------------------------------------------------------------------------
# row helpers
# --------------------------------------------------------------------------
def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aggregate(values: list[float], how: Aggregation) -> float | None:
    if how is Aggregation.COUNT:
        return float(len(values))
    if not values:
        return None
    if how is Aggregation.SUM:
        return round(sum(values), 6)
    if how is Aggregation.MEAN:
        return round(sum(values) / len(values), 6)
    if how is Aggregation.MIN:
        return min(values)
    if how is Aggregation.MAX:
        return max(values)
    return None
