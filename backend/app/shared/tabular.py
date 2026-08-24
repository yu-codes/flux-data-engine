"""Arrow-backed tabular payloads.

External formats (CSV, Excel, JSON, NDJSON, Parquet, SQL, REST) are normalised
into a single in-memory representation before anything else in the platform
touches them. Nothing downstream - visualisation, model execution, results -
may depend on the original file format.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import FieldSpec, FieldType
from .table_ops import (
    column_profile,
    column_stats,
    distinct_values,
    drop_duplicates,
    filter_table,
    group_aggregate,
    numeric_values,
    sort_table,
)

_ARROW_TO_FIELD: list[tuple[Any, FieldType]] = [
    (pa.types.is_boolean, FieldType.BOOLEAN),
    (pa.types.is_integer, FieldType.INTEGER),
    (pa.types.is_floating, FieldType.FLOAT),
    (pa.types.is_decimal, FieldType.FLOAT),
    (pa.types.is_temporal, FieldType.TIMESTAMP),
    (pa.types.is_string, FieldType.STRING),
    (pa.types.is_large_string, FieldType.STRING),
    (pa.types.is_list, FieldType.ARRAY),
    (pa.types.is_large_list, FieldType.ARRAY),
    (pa.types.is_struct, FieldType.JSON),
    (pa.types.is_map, FieldType.JSON),
]


def arrow_type_to_field_type(arrow_type: pa.DataType) -> FieldType:
    for predicate, field_type in _ARROW_TO_FIELD:
        if predicate(arrow_type):
            return field_type
    return FieldType.ANY


@dataclass
class Table:
    """A thin, dependency-light wrapper over :class:`pyarrow.Table`."""

    arrow: pa.Table

    # -- construction ------------------------------------------------------
    @classmethod
    def from_rows(cls, rows: Sequence[dict], columns: Sequence[str] | None = None) -> Table:
        rows = list(rows)
        if not rows:
            names = list(columns or [])
            empty = pa.table({name: pa.array([], type=pa.string()) for name in names})
            return cls(empty)
        names = list(columns) if columns else _ordered_keys(rows)
        data = {
            name: _to_array([_normalise(row.get(name)) for row in rows])
            for name in names
        }
        return cls(pa.table(data))

    @classmethod
    def from_pandas(cls, frame) -> Table:
        return cls(pa.Table.from_pandas(frame, preserve_index=False))

    @classmethod
    def from_parquet(cls, path: str | Path, columns: Sequence[str] | None = None) -> Table:
        """Read a Parquet file, optionally only the columns asked for.

        Projection pushdown is free here - Parquet is columnar, so reading four
        columns of a forty-column file reads a tenth of the bytes - and it is
        the difference between charting a wide table and running out of memory
        doing it.
        """
        return cls(pq.read_table(str(path), columns=list(columns) if columns else None))

    @classmethod
    def parquet_row_count(cls, path: str | Path) -> int:
        """How many rows are in the file, without reading any of them."""
        return int(pq.read_metadata(str(path)).num_rows)

    @classmethod
    def empty(cls) -> Table:
        return cls(pa.table({}))

    # -- inspection --------------------------------------------------------
    @property
    def columns(self) -> list[str]:
        return list(self.arrow.column_names)

    @property
    def num_rows(self) -> int:
        return int(self.arrow.num_rows)

    @property
    def num_columns(self) -> int:
        return int(self.arrow.num_columns)

    def schema_fields(self) -> list[FieldSpec]:
        fields: list[FieldSpec] = []
        for field in self.arrow.schema:
            fields.append(
                FieldSpec(
                    name=field.name,
                    type=arrow_type_to_field_type(field.type),
                    required=not field.nullable,
                    nullable=bool(field.nullable),
                )
            )
        return fields

    # -- conversion --------------------------------------------------------
    def to_rows(self, limit: int | None = None, offset: int = 0) -> list[dict]:
        table = self.arrow
        if offset:
            table = table.slice(offset)
        if limit is not None:
            table = table.slice(0, limit)
        return [_jsonable_row(row) for row in table.to_pylist()]

    def to_pandas(self):
        return self.arrow.to_pandas()

    def select(self, columns: Sequence[str]) -> Table:
        keep = [c for c in columns if c in self.arrow.column_names]
        return Table(self.arrow.select(keep))

    def slice(self, offset: int = 0, length: int | None = None) -> Table:
        return Table(self.arrow.slice(offset, length))

    def rename(self, names: Sequence[str]) -> Table:
        """Rename every column positionally."""
        return Table(self.arrow.rename_columns(list(names)))

    def drop_duplicates(self, columns: Sequence[str] | None = None) -> Table:
        """Keep the first row for each distinct combination of the given columns."""
        return Table(drop_duplicates(self.arrow, list(columns) if columns else None))

    def append_column(self, name: str, values: Iterable[Any]) -> Table:
        array = _to_array([_normalise(v) for v in values])
        arrow = self.arrow
        if name in arrow.column_names:
            arrow = arrow.drop([name])
        return Table(arrow.append_column(name, array))

    def set_column(self, name: str, values: Iterable[Any]) -> Table:
        """Write a column, keeping its position if it already exists.

        `append_column` moves a replaced column to the end, which reorders the
        table - and a step that reorders columns changes what every step after
        it reads. A transform that rewrites a column in place must not do that.
        """
        array = _to_array([_normalise(v) for v in values])
        if name not in self.arrow.column_names:
            return Table(self.arrow.append_column(name, array))
        at = self.arrow.column_names.index(name)
        return Table(self.arrow.set_column(at, name, array))

    def drop(self, columns: Sequence[str]) -> Table:
        """Without the named columns. Names that are not there are ignored."""
        present = [c for c in columns if c in self.arrow.column_names]
        return Table(self.arrow.drop(present)) if present else self

    def take(self, indices: Sequence[int]) -> Table:
        """The rows at these positions, in this order."""
        return Table(self.arrow.take(pa.array(list(indices), type=pa.int64())))

    def where(self, mask: Sequence[bool]) -> Table:
        """The rows the mask keeps.

        The mask is computed from one column rather than from whole rows, so a
        predicate that needs Python semantics still costs one column instead of
        a dict per row - and the filtering itself stays in Arrow.
        """
        return Table(self.arrow.filter(pa.array(list(mask), type=pa.bool_())))

    # -- computation -------------------------------------------------------
    #  These exist so that nothing outside this class has to call `to_rows()`
    #  in order to ask a question. Materialising every row as a dict was the
    #  single thing keeping the platform at demo scale: it happened before
    #  filtering, before sorting and before pagination, so the cost was paid in
    #  full no matter how little was being asked for.

    def filter(self, conditions: Sequence[Mapping[str, Any]]) -> Table:
        """Keep the rows matching every condition."""
        return Table(filter_table(self.arrow, conditions))

    def sort(self, column: str, descending: bool = False) -> Table:
        return Table(sort_table(self.arrow, column, descending))

    def group_aggregate(self, by: str, columns: Sequence[str], how: str) -> Table:
        """Group by one column and aggregate the others."""
        return Table(group_aggregate(self.arrow, by, columns, how))

    def numeric_values(self, column: str) -> list[float]:
        """A column as floats, non-numbers and nulls dropped."""
        return numeric_values(self.arrow, column)

    def distinct(self, column: str) -> list[Any]:
        return distinct_values(self.arrow, column)

    def stats(self, column: str) -> dict[str, Any]:
        return column_stats(self.arrow, column)

    def column_profile(self, column: str, *, max_categories: int = 12) -> dict[str, Any]:
        """Null counts, distribution or top values - whichever the column has."""
        return column_profile(self.arrow, column, max_categories=max_categories)

    def column_values(self, column: str) -> list[Any]:
        """One column as Python values, without building a dict per row.

        A column that is not there answers with nulls rather than with nothing,
        because that is what `row.get(name)` did and several callers depend on
        it - a chart split by a column the dataset lacks used to draw one
        unnamed band, and should not start raising instead. Callers that care
        whether the column exists ask `columns` first.
        """
        if column not in self.arrow.column_names:
            return [None] * self.num_rows
        return [_jsonable(v) for v in self.arrow.column(column).to_pylist()]

    # -- persistence -------------------------------------------------------
    def write_parquet(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(self.arrow, str(target), compression="snappy")
        return target


def _to_array(values: list[Any]) -> pa.Array:
    """Build a column, degrading to text when a column mixes incompatible types.

    Real-world sources are untidy - a field that is numeric in most records and
    a string in a few. Falling back to text keeps ingestion working instead of
    failing the whole dataset; the inferred schema then reports it as a string.
    """
    try:
        return pa.array(values)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError, OverflowError):
        return pa.array(
            [None if v is None else _as_text(v) for v in values], type=pa.string()
        )


def _as_text(value: Any) -> str:
    import json

    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _ordered_keys(rows: Sequence[dict]) -> list[str]:
    """Union of keys across rows, first-seen order preserved."""
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def _normalise(value: Any) -> Any:
    """Make a Python value safe to hand to Arrow."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dict, list, tuple)):
        return value
    return value


def _jsonable_row(row: dict) -> dict:
    return {key: _jsonable(value) for key, value in row.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
