"""Table operations that run in Arrow instead of in Python loops.

The platform used to answer every question by calling `Table.to_rows()` and
then looping: filtering, sorting, grouping and profiling all materialised the
whole dataset as a list of dicts first. That put a hard ceiling on the product
- a few hundred thousand rows and it stops - and it made the Explore page's
pagination a fiction, because the rows were already all in memory before the
page was sliced.

Everything here is `pyarrow.compute`, which the project already depends on for
Parquet. No new engine, no new query language: the same operations, moved down
a layer to where the data already is.

The one thing that is not obvious is why the comparisons are so careful about
types. A filter value arrives from a text box, so it is always a string, while
the column may be numbers, timestamps or booleans. The old row-based code
papered over that with `str(a) == str(b)`. Arrow is typed and will not compare
across types at all, so the value is coerced to the column's type where that is
possible, and both sides fall back to their printed form where it is not -
which reproduces what a person filtering a table actually means.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from .errors import ValidationError

OPERATORS = (
    "eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "is_null", "not_null"
)

#  Aggregations the platform offers, mapped to Arrow's hash aggregate names.
AGGREGATIONS = {
    "count": "count",
    "sum": "sum",
    "mean": "mean",
    "min": "min",
    "max": "max",
}


# --------------------------------------------------------------------------
# filtering
# --------------------------------------------------------------------------
def filter_table(table: pa.Table, conditions: Sequence[Mapping[str, Any]]) -> pa.Table:
    """Apply every condition, in order. An unknown column or operator is an error."""
    for spec in conditions:
        column = spec.get("column")
        operator = spec.get("op", "eq")
        if not column or operator not in OPERATORS:
            raise ValidationError(
                f"invalid filter {dict(spec)!r}",
                details={"operators": sorted(OPERATORS)},
            )
        if column not in table.column_names:
            raise ValidationError(
                f"no column '{column}' to filter on",
                details={"columns": table.column_names},
            )
        mask = _mask(table, column, operator, spec.get("value"))
        #  A null never satisfies a comparison, which is what the row version
        #  did by testing `a is not None` first.
        table = table.filter(mask, null_selection_behavior="drop")
    return table


def _mask(table: pa.Table, column: str, operator: str, value: Any):
    array = table.column(column)

    if operator == "is_null":
        return pc.is_null(array)
    if operator == "not_null":
        return pc.is_valid(array)

    if operator == "contains":
        if value is None:
            return pa.array([False] * table.num_rows, type=pa.bool_())
        text = pc.utf8_lower(_as_string(array))
        #  A null column value read as "" in the row version, so it matched
        #  only an empty needle; filling keeps that behaviour.
        return pc.match_substring(pc.fill_null(text, ""), str(value).lower())

    if operator == "in":
        options = list(value or [])
        if not options:
            return pa.array([False] * table.num_rows, type=pa.bool_())
        coerced, ok = _coerce_many(options, array.type)
        if ok:
            return pc.is_in(array, value_set=pa.array(coerced, type=array.type))
        wanted = pa.array([_printed(v) for v in options], type=pa.string())
        return pc.is_in(pc.fill_null(_as_string(array), ""), value_set=wanted)

    coerced, ok = _coerce(value, array.type)
    if ok:
        compare = getattr(pc, _COMPARISONS[operator])
        return compare(array, pa.scalar(coerced, type=array.type))

    #  Types that cannot meet: compare what a person would see.
    left = pc.fill_null(_as_string(array), "")
    right = pa.scalar(_printed(value), type=pa.string())
    return getattr(pc, _COMPARISONS[operator])(left, right)


_COMPARISONS = {
    "eq": "equal",
    "ne": "not_equal",
    "gt": "greater",
    "gte": "greater_equal",
    "lt": "less",
    "lte": "less_equal",
}


def _as_string(array) -> Any:
    if pa.types.is_string(array.type) or pa.types.is_large_string(array.type):
        return array
    return pc.cast(array, pa.string(), safe=False)


def _printed(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        #  "1.0" typed against an integer column means 1.
        return str(int(value))
    return str(value)


def _coerce(value: Any, target: pa.DataType) -> tuple[Any, bool]:
    """Turn a filter value into something Arrow can compare with this column."""
    if value is None:
        return None, False
    try:
        if pa.types.is_boolean(target):
            if isinstance(value, bool):
                return value, True
            text = str(value).strip().lower()
            if text in ("true", "1", "yes"):
                return True, True
            if text in ("false", "0", "no"):
                return False, True
            return None, False
        if pa.types.is_integer(target):
            return int(float(str(value))), True
        if pa.types.is_floating(target) or pa.types.is_decimal(target):
            return float(str(value)), True
        if pa.types.is_string(target) or pa.types.is_large_string(target):
            return _printed(value), True
        if pa.types.is_temporal(target):
            #  Let Arrow parse it; a bad string simply falls through to text.
            pc.cast(pa.scalar(str(value)), target)
            return str(value), False
    except (ValueError, TypeError, pa.ArrowInvalid, pa.ArrowNotImplementedError):
        return None, False
    return None, False


def _coerce_many(values: Sequence[Any], target: pa.DataType) -> tuple[list, bool]:
    out = []
    for value in values:
        coerced, ok = _coerce(value, target)
        if not ok:
            return [], False
        out.append(coerced)
    return out, True


# --------------------------------------------------------------------------
# ordering
# --------------------------------------------------------------------------
def sort_table(table: pa.Table, column: str, descending: bool = False) -> pa.Table:
    """Order by one column, nulls last either way.

    A deliberate change from the row-based version, which reversed its whole
    key on a descending sort and so floated every null to the top: asking for
    "highest first" filled the first screen with rows that have no value at
    all. A missing value is not a large value, it is an absent one, so it goes
    last in both directions.
    """
    if column not in table.column_names:
        raise ValidationError(
            f"no column '{column}' to sort by", details={"columns": table.column_names}
        )
    order = "descending" if descending else "ascending"
    #  Null placement travels with the sort key; passing it separately is
    #  deprecated in current Arrow and warns on every call.
    indices = pc.sort_indices(table, sort_keys=[(column, order, "at_end")])
    return table.take(indices)


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def group_aggregate(
    table: pa.Table,
    by: str,
    columns: Sequence[str],
    how: str,
) -> pa.Table:
    """One grouping column, one aggregation applied to several value columns.

    Returns a table of `by` plus one column per aggregate, named
    `<column>_<how>` - Arrow's own convention, kept rather than renamed so the
    result is predictable from the call.
    """
    if by not in table.column_names:
        raise ValidationError(
            f"no column '{by}' to group by", details={"columns": table.column_names}
        )
    if how not in AGGREGATIONS:
        raise ValidationError(
            f"unknown aggregation '{how}'", details={"supported": sorted(AGGREGATIONS)}
        )

    present = [c for c in columns if c in table.column_names]
    missing = [c for c in columns if c not in table.column_names]
    if missing:
        raise ValidationError(
            f"no column {missing[0]!r} to aggregate",
            details={"columns": table.column_names},
        )

    #  Counting means counting rows in the group, not the non-null values in
    #  some column, so it counts the grouping column itself with nulls kept and
    #  answers with a single `count` column whatever was asked to be counted.
    if how == "count":
        return table.group_by(by).aggregate(
            [(by, "count", pc.CountOptions(mode="all"))]
        ).rename_columns([by, "count"])

    numeric = _numeric_view(table, present)
    return numeric.group_by(by).aggregate([(c, AGGREGATIONS[how]) for c in present])


def _numeric_view(table: pa.Table, columns: Sequence[str]) -> pa.Table:
    """Cast the columns being aggregated to float, nulling what will not cast.

    A column of numbers stored as text is extremely common in real files, and
    the row-based code coped by calling `float()` on each value. Casting the
    whole column once does the same thing far more cheaply.
    """
    arrays = {}
    for name in table.column_names:
        column = table.column(name)
        if name in columns:
            column = to_float(column)
        arrays[name] = column
    return pa.table(arrays)


def to_float(array):
    """A float view of a column, with anything unparseable turned into null.

    Arrow's cast raises on the first value it cannot read rather than nulling
    it, which is the wrong behaviour here: one "n/a" in a column of forty
    thousand numbers should not fail the whole aggregate. The vectorised cast
    is still tried first because it is the common case and it is fast; only a
    column that actually contains junk pays for the element-wise pass, and it
    pays once rather than once per row per question.
    """
    if pa.types.is_floating(array.type):
        return array
    if pa.types.is_boolean(array.type):
        #  A boolean is not a measurement; averaging True and False is nonsense.
        return pa.nulls(len(array), type=pa.float64())
    try:
        return pc.cast(array, pa.float64(), safe=False)
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        pass
    return pa.array([_as_float(v) for v in array.to_pylist()], type=pa.float64())


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# deduplication
# --------------------------------------------------------------------------
def drop_duplicates(table: pa.Table, columns: Sequence[str] | None = None) -> pa.Table:
    """Keep the first row of each distinct key, in the original order.

    "First" and "in order" are both part of the contract: a pipeline step that
    reorders its output silently changes what every step after it sees.
    Grouping alone would not preserve either, so the row indices are gathered
    and the minimum one per group is taken.
    """
    keys = list(columns) if columns else list(table.column_names)
    missing = [c for c in keys if c not in table.column_names]
    if missing:
        raise ValidationError(
            f"no column {missing[0]!r} to deduplicate on",
            details={"columns": table.column_names},
        )
    if table.num_rows == 0:
        return table

    #  Keys are compared as text so that a column of mixed or nested values
    #  behaves the way the row-based version did, where the key was a tuple of
    #  `str(value)`.
    indexed = table.append_column(
        "__row", pa.array(range(table.num_rows), type=pa.int64())
    )
    stringified = indexed
    for name in keys:
        stringified = stringified.set_column(
            stringified.column_names.index(name),
            name,
            pc.fill_null(_as_string(stringified.column(name)), "\u0000null"),
        )
    firsts = stringified.group_by(keys).aggregate([("__row", "min")])
    wanted = pc.array_sort_indices(firsts.column("__row_min"))
    order = firsts.column("__row_min").take(wanted)
    return table.take(order)


# --------------------------------------------------------------------------
# reading values back
# --------------------------------------------------------------------------
def numeric_values(table: pa.Table, column: str) -> list[float]:
    """Every value in a column that is a number, as floats, nulls dropped."""
    if column not in table.column_names:
        return []
    return [v for v in pc.drop_null(to_float(table.column(column))).to_pylist()]


def distinct_values(table: pa.Table, column: str) -> list[Any]:
    if column not in table.column_names:
        return []
    return pc.unique(table.column(column).combine_chunks()).to_pylist()


def column_profile(
    table: pa.Table, column: str, *, max_categories: int = 12
) -> dict[str, Any]:
    """Everything the Explore page shows about one column, in one pass.

    Numbers get distribution statistics; anything else gets its commonest
    values. Which of the two a column receives is decided by whether it holds
    numbers, not by its declared type - a column of numerals stored as text is
    still a measurement, and the old row-based profiler treated it as one.
    """
    array = table.column(column)
    total = table.num_rows
    nulls = int(pc.sum(pc.cast(pc.is_null(array), pa.int64())).as_py() or 0)

    entry: dict[str, Any] = {
        "null_count": nulls,
        "null_ratio": round(nulls / total, 4) if total else 0.0,
        "distinct_count": _distinct_count(array),
    }

    numbers = pc.drop_null(to_float(array))
    if len(numbers):
        entry.update(
            {
                "min": _plain(pc.min(numbers)),
                "max": _plain(pc.max(numbers)),
                "mean": round(float(pc.mean(numbers).as_py()), 6),
                #  Arrow's approximate_median is the streaming t-digest; over a
                #  profile of a whole column that is the right trade, and the
                #  exact value would mean sorting the column.
                "median": _plain(pc.approximate_median(numbers)),
                "stddev": round(float(pc.stddev(numbers, ddof=1).as_py() or 0.0), 6),
            }
        )
    elif total - nulls:
        entry["top_values"] = _top_values(array, max_categories)
    return entry


def _distinct_count(array) -> int:
    try:
        return int(pc.count_distinct(array).as_py())
    except pa.ArrowNotImplementedError:
        #  Nested columns cannot be hashed; fall back to their printed form.
        return int(pc.count_distinct(_as_string(array)).as_py())


def _top_values(array, limit: int) -> list[dict[str, Any]]:
    try:
        counts = pc.value_counts(array.combine_chunks())
    except pa.ArrowNotImplementedError:
        counts = pc.value_counts(_as_string(array).combine_chunks())
    pairs = [
        {"value": entry["values"], "count": int(entry["counts"])}
        for entry in counts.to_pylist()
        if entry["values"] is not None
    ]
    pairs.sort(key=lambda pair: pair["count"], reverse=True)
    return pairs[:limit]


def _plain(scalar) -> Any:
    """An Arrow scalar as a Python number, integral where it truly is one."""
    value = scalar.as_py()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def column_stats(table: pa.Table, column: str) -> dict[str, Any]:
    """Min/max/mean for a column, computed in Arrow.

    Returns an empty dict for a column with nothing numeric in it, so callers
    can treat "not a number column" and "no rows" the same way.
    """
    if column not in table.column_names:
        return {}
    values = pc.drop_null(to_float(table.column(column)))
    if len(values) == 0:
        return {}
    return {
        "min": float(pc.min(values).as_py()),
        "max": float(pc.max(values).as_py()),
        "mean": round(float(pc.mean(values).as_py()), 6),
    }
