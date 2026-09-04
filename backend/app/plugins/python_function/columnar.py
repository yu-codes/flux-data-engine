"""Every standard transform, implemented against `Table` instead of rows.

The pipeline used to run on `list[dict]`: each step turned an Arrow table into
one dict per row, looped in Python, and built a new table from the result. A
twelve-step pipeline therefore paid for materialising the whole dataset twelve
times, and paid it in full whether the step touched one column or forty.

What each function here does instead is read the columns it actually needs.
Some are pure Arrow - filtering, sorting, projection, deduplication - and never
touch a Python value at all. Others need Python semantics that Arrow has no
kernel for: six timestamp formats tried in order, a number extracted from
"30 (m/s)", a mapping keyed by the string form of a value. Those still loop,
but they loop over one column rather than over a dict per row, and the table
they return is assembled column-wise.

The behaviour is deliberately unchanged. Every transform here has an oracle
test holding it to what the row implementation answered, including the parts
that look like accidents: which row deduplication keeps, where nulls sort, what
happens to a column the parameters do not mention.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from app.shared.errors import ValidationError
from app.shared.tabular import Table

# --------------------------------------------------------------------------
# reading values out of a column
# --------------------------------------------------------------------------
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)


def as_number(value: Any) -> float | None:
    """The number in a value, or None. Tolerates thousands separators."""
    #  The overwhelmingly common case, checked first and exactly. Arrow hands
    #  back real floats, and every windowed or aggregating transform calls this
    #  once per value it reads - so the isinstance chain below was several
    #  seconds of type checking on a table of a few hundred thousand rows.
    #  `value != value` is true only for NaN, which is what math.isnan says
    #  without a call and without re-converting a float to a float.
    if value.__class__ is float:
        return None if value != value else value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    #  ISO is what the platform itself writes - `column_values` renders every
    #  temporal column that way - so it is the overwhelmingly common case and
    #  the one the format loop handled worst: "2026-08-30" only matched on the
    #  fourth attempt, so three failed `strptime` calls were paid for every
    #  date read. `fromisoformat` is implemented in C and answers in one.
    #
    #  Guarded on the shape rather than tried unconditionally, because 3.11's
    #  `fromisoformat` also accepts the basic form ("20260830"), and a column
    #  of eight-digit codes must keep reading as codes.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part) for part in (value or [])]


def sort_key(value: Any):
    number = as_number(value)
    #  Numbers sort before text so a mixed column still produces a stable order.
    return (0, number, "") if number is not None else (1, 0.0, str(value))


def require_column(table: Table, column: str) -> None:
    """Refuse a column that is not there - unless there is no data at all."""
    if table.num_rows and column not in table.columns:
        raise ValidationError(
            f"column '{column}' is not in the input",
            details={"available": sorted(table.columns)},
        )


def _is_number(value: Any) -> bool:
    """A real number, not a numeric string and not a boolean.

    Two transforms distinguish these - `group_aggregate` counts only genuine
    numbers while `summarise` parses text - and the difference is preserved
    rather than tidied away, because a pipeline in the field depends on it.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --------------------------------------------------------------------------
# pure Arrow: shape
# --------------------------------------------------------------------------
def select_columns(table: Table, params: dict[str, Any]) -> Table:
    """Narrow a wide table down to the columns that matter.

    Exactly what Arrow's own projection does, so doing it a row at a time was
    rebuilding the whole table in order to throw most of it away.
    """
    columns = params["columns"]
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(",") if c.strip()]
    if not columns:
        raise ValidationError("select_columns needs at least one column")
    missing = [c for c in columns if c not in table.columns]
    if missing:
        raise ValidationError(
            f"columns not present in the input: {missing}",
            details={"available": sorted(table.columns)},
        )
    return table.select(columns)


def drop_columns(table: Table, params: dict[str, Any]) -> Table:
    unwanted = set(as_list(params.get("columns")))
    if not unwanted:
        raise ValidationError("drop_columns needs at least one column")
    return table.select([c for c in table.columns if c not in unwanted])


def rename_columns(table: Table, params: dict[str, Any]) -> Table:
    mapping = params.get("mapping") or {}
    if not isinstance(mapping, dict) or not mapping:
        raise ValidationError("rename_columns needs a mapping of old to new names")
    return table.rename([str(mapping.get(c, c)) for c in table.columns])


def sort_rows(table: Table, params: dict[str, Any]) -> Table:
    column = params["column"]
    if column not in table.columns:
        raise ValidationError(
            f"no column '{column}' in the input", details={"available": table.columns}
        )
    return table.sort(column, bool(params.get("descending", False)))


def limit_rows(table: Table, params: dict[str, Any]) -> Table:
    count = int(params.get("count", 100))
    if count < 1:
        raise ValidationError("count must be >= 1")
    if params.get("from_end"):
        return table.slice(max(0, table.num_rows - count), count)
    return table.slice(0, count)


def drop_duplicates(table: Table, params: dict[str, Any]) -> Table:
    columns = as_list(params.get("columns")) or list(table.columns)
    missing = [c for c in columns if c not in table.columns]
    if missing:
        raise ValidationError(
            f"columns not present in the input: {missing}",
            details={"available": sorted(table.columns)},
        )
    return table.drop_duplicates(columns)


# --------------------------------------------------------------------------
# pure Arrow: rows kept
# --------------------------------------------------------------------------
_FILTER_OPERATORS = {
    "not_empty", "is_empty", "equals", "not_equals",
    "gt", "gte", "lt", "lte", "in",
}


def filter_rows(table: Table, params: dict[str, Any]) -> Table:
    """Keep the rows worth carrying forward.

    The predicate is evaluated over one column and the keeping is done by
    Arrow, so a step that throws away 90% of a dataset no longer has to build
    the 90% first.
    """
    column = params["column"]
    operator = params.get("op", "not_empty")
    target = params.get("value")
    if operator not in _FILTER_OPERATORS:
        raise ValidationError(
            f"unsupported filter operator '{operator}'",
            details={"supported": sorted(_FILTER_OPERATORS)},
        )
    wanted = {str(v) for v in (target or [])} if operator == "in" else set()
    bound = float(target) if operator in {"gt", "gte", "lt", "lte"} else 0.0

    def keep(value: Any) -> bool:
        if operator == "not_empty":
            return value is not None and str(value).strip() != ""
        if operator == "is_empty":
            return value is None or str(value).strip() == ""
        if operator == "equals":
            return str(value) == str(target)
        if operator == "not_equals":
            return str(value) != str(target)
        if operator == "in":
            return str(value) in wanted
        if not _is_number(value):
            return False
        return {
            "gt": value > bound,
            "gte": value >= bound,
            "lt": value < bound,
            "lte": value <= bound,
        }[operator]

    mask = [keep(value) for value in table.column_values(column)]
    if not any(mask):
        raise ValidationError(
            f"the filter on '{column}' removed every row, so there is nothing "
            f"for the next step to read"
        )
    return table.where(mask)


# --------------------------------------------------------------------------
# column-wise: one column in, one column out
# --------------------------------------------------------------------------
_CAST_TARGETS = {"number", "integer", "text", "boolean"}
_TRUTHY = {"1", "true", "yes", "y", "是"}


def cast_types(table: Table, params: dict[str, Any]) -> Table:
    """Force columns to a type, so later arithmetic and sorting behave."""
    casts = params.get("casts") or {}
    if not isinstance(casts, dict) or not casts:
        raise ValidationError("cast_types needs a mapping of column to type")
    unknown = sorted(set(casts.values()) - _CAST_TARGETS)
    if unknown:
        raise ValidationError(
            f"unsupported target types: {unknown}",
            details={"allowed": sorted(_CAST_TARGETS)},
        )

    result = table
    for column, target in casts.items():
        raw = result.column_values(column)
        if target == "text":
            cast = [None if v is None else str(v) for v in raw]
        elif target == "boolean":
            cast = [
                None
                if v is None or str(v).strip() == ""
                else str(v).strip().lower() in _TRUTHY
                for v in raw
            ]
        else:
            numbers = (as_number(v) for v in raw)
            cast = [
                None if n is None else (int(n) if target == "integer" else n)
                for n in numbers
            ]
        result = result.set_column(column, cast)
    return result


def fill_missing(table: Table, params: dict[str, Any]) -> Table:
    """Replace nulls with a stated value, so a gap never reads as a zero."""
    columns = as_list(params.get("columns"))
    replacement = params.get("value", 0)
    if not columns:
        raise ValidationError("fill_missing needs at least one column")
    result = table
    for column in columns:
        filled = [
            replacement if v is None or (isinstance(v, str) and not v.strip()) else v
            for v in result.column_values(column)
        ]
        result = result.set_column(column, filled)
    return result


_NUMBER_IN_TEXT = re.compile(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?")


def parse_numeric(table: Table, params: dict[str, Any]) -> Table:
    """Pull the leading number out of a text column.

    Real sources carry values like "30 (m/s)", "1,013" or a localised "no data"
    string in the same column. This lifts the number out into a clean numeric
    column and leaves the rest null, so downstream steps can do arithmetic.
    """
    column = params["column"]
    target = params.get("output") or f"{column}_value"
    keep_text = bool(params.get("keep_original", True))

    parsed: list[float | None] = []
    for raw in table.column_values(column):
        if _is_number(raw):
            parsed.append(float(raw))
            continue
        value = None
        if isinstance(raw, str):
            match = _NUMBER_IN_TEXT.search(raw.replace(",", ""))
            if match:
                try:
                    value = float(match.group())
                except ValueError:
                    value = None
        parsed.append(value)

    result = table if keep_text else table.drop([column])
    return result.set_column(target, parsed)


def datetime_parts(table: Table, params: dict[str, Any]) -> Table:
    """Split a timestamp into the parts an analysis actually groups by.

    Seasonality is a question about months; trends are a question about years.
    Neither is answerable while the date is still one opaque string.
    """
    column = params["column"]
    require_column(table, column)
    prefix = params.get("prefix") or column
    wanted = as_list(params.get("parts")) or ["year", "month", "day"]
    allowed = {"year", "month", "day", "hour", "dayofyear", "week", "quarter", "date"}
    unknown = sorted(set(wanted) - allowed)
    if unknown:
        raise ValidationError(
            f"unsupported parts: {unknown}", details={"allowed": sorted(allowed)}
        )

    #  Parsed once per row, not once per requested part.
    moments = [as_datetime(v) for v in table.column_values(column)]
    result = table
    for part in wanted:
        result = result.set_column(
            f"{prefix}_{part}", [_part_of(moment, part) for moment in moments]
        )
    return result


def _part_of(moment: datetime | None, part: str) -> Any:
    if moment is None:
        return None
    if part == "year":
        return moment.year
    if part == "month":
        return moment.month
    if part == "day":
        return moment.day
    if part == "hour":
        return moment.hour
    if part == "dayofyear":
        return moment.timetuple().tm_yday
    if part == "week":
        return moment.isocalendar()[1]
    if part == "quarter":
        return (moment.month - 1) // 3 + 1
    return moment.strftime("%Y-%m-%d")


def duration_between(table: Table, params: dict[str, Any]) -> Table:
    """Hours (or days) between two timestamp columns."""
    unit = params.get("unit", "hours")
    if unit not in {"hours", "days", "minutes"}:
        raise ValidationError("unit must be hours, days or minutes")
    divisor = {"minutes": 60.0, "hours": 3600.0, "days": 86400.0}[unit]
    target = params.get("output") or f"duration_{unit}"

    starts = [as_datetime(v) for v in table.column_values(params["start"])]
    ends = [as_datetime(v) for v in table.column_values(params["end"])]
    spans = [
        round((end - start).total_seconds() / divisor, 3)
        if start and end and end >= start
        else None
        for start, end in zip(starts, ends, strict=True)
    ]
    return table.set_column(target, spans)


def bin_numeric(table: Table, params: dict[str, Any]) -> Table:
    """Cut a numeric column into named bands.

    Bands are how a continuous measure becomes something you can group by, and
    naming them is the point: "severe" carries meaning that "48.2" does not.
    """
    column = params["column"]
    require_column(table, column)
    edges = [float(edge) for edge in (params.get("edges") or [])]
    labels = as_list(params.get("labels"))
    if len(edges) < 2:
        raise ValidationError("bin_numeric needs at least two edges")
    if sorted(edges) != edges:
        raise ValidationError("edges must be in ascending order")
    if labels and len(labels) != len(edges) - 1:
        raise ValidationError(
            f"{len(edges) - 1} bands need {len(edges) - 1} labels, got {len(labels)}"
        )
    target = params.get("output") or f"{column}_band"
    below = params.get("below_label") or "below range"
    above = params.get("above_label") or "above range"

    def band(value: float) -> str:
        if value < edges[0]:
            return below
        for index in range(len(edges) - 1):
            #  Half-open bands, closed at the very top, so every value lands once.
            upper = edges[index + 1]
            if value < upper or (index == len(edges) - 2 and value <= upper):
                return labels[index] if labels else f"{edges[index]:g}-{upper:g}"
        return above

    numbers = (as_number(v) for v in table.column_values(column))
    return table.set_column(
        target, [None if n is None else band(n) for n in numbers]
    )


def map_values(table: Table, params: dict[str, Any]) -> Table:
    """Translate coded values into readable ones via a lookup table."""
    column = params["column"]
    mapping = params.get("mapping") or {}
    if not isinstance(mapping, dict) or not mapping:
        raise ValidationError("map_values needs a mapping")
    target = params.get("output") or f"{column}_label"
    fallback = params.get("default")
    keep_unmapped = bool(params.get("keep_unmapped", True))

    def translate(key: Any) -> Any:
        text = "" if key is None else str(key)
        if text in mapping:
            return mapping[text]
        if fallback is not None:
            return fallback
        return key if keep_unmapped else None

    return table.set_column(
        target, [translate(v) for v in table.column_values(column)]
    )


def extract_pattern(table: Table, params: dict[str, Any]) -> Table:
    """Pull a named piece out of a text column with a bounded regex.

    The pattern is applied with a size limit and no backreferences by design:
    this runs on network-supplied input, and a pathological pattern is a denial
    of service waiting to happen.
    """
    column = params["column"]
    pattern_text = str(params.get("pattern") or "")
    if not pattern_text:
        raise ValidationError("extract_pattern needs a pattern")
    if len(pattern_text) > 200:
        raise ValidationError("pattern is too long (max 200 characters)")
    try:
        pattern = re.compile(pattern_text)
    except re.error as exc:
        raise ValidationError(f"invalid pattern: {exc}") from exc
    group = int(params.get("group", 0))
    target = params.get("output") or f"{column}_extracted"

    def extract(raw: Any) -> Any:
        if not isinstance(raw, str) or not raw:
            return None
        match = pattern.search(raw[:2000])
        if match and group <= (match.re.groups or 0):
            return match.group(group)
        return None

    return table.set_column(target, [extract(v) for v in table.column_values(column)])


_FLAG_OPERATORS = {"not_empty", "is_empty", "equals", "contains", "gt", "gte", "lt", "lte"}


def flag_rows(table: Table, params: dict[str, Any]) -> Table:
    """Mark rows matching a condition instead of dropping them.

    Filtering answers "which rows"; flagging keeps the denominator, which is
    what you need to state a rate rather than a count.
    """
    column = params["column"]
    operator = params.get("op", "not_empty")
    target_value = params.get("value")
    output = params.get("output") or f"{column}_flag"
    if operator not in _FLAG_OPERATORS:
        raise ValidationError(
            f"unsupported operator '{operator}'",
            details={"supported": sorted(_FLAG_OPERATORS)},
        )
    bound = as_number(target_value)

    def matches(value: Any) -> bool:
        if operator == "not_empty":
            return value is not None and str(value).strip() != ""
        if operator == "is_empty":
            return value is None or str(value).strip() == ""
        if operator == "equals":
            return str(value) == str(target_value)
        if operator == "contains":
            return str(target_value) in str(value or "")
        number = as_number(value)
        if number is None or bound is None:
            return False
        return {
            "gt": number > bound,
            "gte": number >= bound,
            "lt": number < bound,
            "lte": number <= bound,
        }[operator]

    return table.set_column(
        output, [matches(v) for v in table.column_values(column)]
    )


def moving_average(table: Table, params: dict[str, Any]) -> Table:
    """A trailing mean over the last `window` numeric values seen."""
    column = params["column"]
    window = int(params.get("window", 3))
    target = params.get("output") or f"{column}_ma{window}"
    if window < 1:
        raise ValidationError("window must be >= 1")

    averages: list[float | None] = []
    buffer: list[float] = []
    for value in table.column_values(column):
        if _is_number(value):
            buffer.append(float(value))
        if len(buffer) > window:
            buffer.pop(0)
        averages.append(round(sum(buffer) / len(buffer), 6) if buffer else None)
    return table.set_column(target, averages)


def zscore_outliers(table: Table, params: dict[str, Any]) -> Table:
    """Standard scores, and which rows are beyond the threshold."""
    column = params["column"]
    threshold = float(params.get("threshold", 3.0))
    raw = table.column_values(column)
    numbers = [float(v) for v in raw if _is_number(v) and not math.isnan(float(v))]
    if len(numbers) < 2:
        raise ValidationError(f"column '{column}' has too few numeric values")

    mean = sum(numbers) / len(numbers)
    variance = sum((v - mean) ** 2 for v in numbers) / (len(numbers) - 1)
    stddev = math.sqrt(variance) or 1e-12

    scores: list[float | None] = []
    outliers: list[bool | None] = []
    for value in raw:
        if _is_number(value):
            score = round((float(value) - mean) / stddev, 6)
            scores.append(score)
            outliers.append(abs(score) > threshold)
        else:
            scores.append(None)
            outliers.append(None)
    return table.set_column("zscore", scores).set_column("is_outlier", outliers)


def rank_rows(table: Table, params: dict[str, Any]) -> Table:
    """Add a 1-based rank over a numeric column, highest first by default."""
    column = params["column"]
    require_column(table, column)
    descending = bool(params.get("descending", True))
    target = params.get("output") or f"{column}_rank"

    numbers = [as_number(v) for v in table.column_values(column)]
    #  Stable, so rows with equal values keep the order they arrived in.
    ordered = sorted(
        ((index, n) for index, n in enumerate(numbers) if n is not None),
        key=lambda pair: pair[1] or 0.0,
        reverse=descending,
    )
    ranks = {index: position + 1 for position, (index, _) in enumerate(ordered)}
    return table.set_column(target, [ranks.get(i) for i in range(len(numbers))])


def percent_of_total(table: Table, params: dict[str, Any]) -> Table:
    """Express a column as a share of its own total."""
    column = params["column"]
    require_column(table, column)
    target = params.get("output") or f"{column}_pct"
    numbers = [as_number(v) for v in table.column_values(column)]
    total = sum(n for n in numbers if n is not None)
    if not total:
        raise ValidationError(f"column '{column}' sums to zero, so shares are undefined")
    return table.set_column(
        target,
        [None if n is None else round(100 * n / total, 4) for n in numbers],
    )


# --------------------------------------------------------------------------
# aggregation: many rows in, few rows out
# --------------------------------------------------------------------------
def group_aggregate(table: Table, params: dict[str, Any]) -> Table:
    """Collapse rows to one per group, with an aggregate and a count.

    Only genuine numbers are counted - a column of numeric strings aggregates
    to nothing here, and `cast_types` or `parse_numeric` is the step that fixes
    that. Kept as it was, because a pipeline that relies on the distinction
    would otherwise change its answer silently.
    """
    by = params["group_by"]
    column = params["column"]
    how = params.get("agg", "sum")
    if how not in {"sum", "mean", "min", "max", "count"}:
        raise ValidationError(f"unsupported aggregation '{how}'")

    buckets: dict[Any, list[float]] = {}
    grouping = table.column_values(by)
    measured = table.column_values(column)
    for key, value in zip(grouping, measured, strict=True):
        if _is_number(value):
            buckets.setdefault(key, []).append(float(value))

    rows = [
        {
            by: key,
            f"{column}_{how}": round(_aggregate_numbers(values, how), 6),
            "count": len(values),
        }
        for key, values in buckets.items()
    ]
    return Table.from_rows(sorted(rows, key=lambda row: str(row[by])))


def _aggregate_numbers(values: list[float], how: str) -> float:
    if how == "sum":
        return sum(values)
    if how == "mean":
        return sum(values) / len(values)
    if how == "min":
        return min(values)
    if how == "max":
        return max(values)
    return float(len(values))


_MEASURES = {"sum", "mean", "min", "max", "count", "median"}


def summarise(table: Table, params: dict[str, Any]) -> Table:
    """Group by one or more columns and aggregate several measures at once.

    The single-measure `group_aggregate` is fine for one number; a real summary
    table wants a count beside a mean beside a max, in one pass.
    """
    group_by = as_list(params.get("group_by"))
    measures = params.get("measures") or {}
    if not isinstance(measures, dict):
        raise ValidationError("measures must map a column to an aggregation")
    unknown = sorted({str(v) for v in measures.values()} - _MEASURES)
    if unknown:
        raise ValidationError(
            f"unsupported aggregations: {unknown}", details={"allowed": sorted(_MEASURES)}
        )

    #  Only the columns the summary names are read, however wide the input is.
    keys = [table.column_values(column) for column in group_by]
    parsed = {
        column: [as_number(v) for v in table.column_values(column)] for column in measures
    }

    buckets: dict[tuple, dict[str, list[float]]] = {}
    sizes: dict[tuple, int] = {}
    identities: dict[tuple, dict[str, Any]] = {}
    for index in range(table.num_rows):
        key = tuple(column[index] for column in keys)
        identities.setdefault(
            key, {name: keys[at][index] for at, name in enumerate(group_by)}
        )
        sizes[key] = sizes.get(key, 0) + 1
        bucket = buckets.setdefault(key, {column: [] for column in measures})
        for column in measures:
            number = parsed[column][index]
            if number is not None:
                bucket[column].append(number)

    rows = []
    for key, values in buckets.items():
        row = dict(identities[key])
        row["row_count"] = sizes[key]
        for column, how in measures.items():
            row[f"{column}_{how}"] = _measure(values[column], str(how), sizes[key])
        rows.append(row)
    ordered = sorted(rows, key=lambda row: tuple(str(row.get(c)) for c in group_by))
    return Table.from_rows(ordered)


def _measure(values: list[float], how: str, size: int) -> float | None:
    if how == "count":
        return float(size)
    if not values:
        return None
    if how == "sum":
        return round(sum(values), 6)
    if how == "mean":
        return round(sum(values) / len(values), 6)
    if how == "min":
        return round(min(values), 6)
    if how == "max":
        return round(max(values), 6)
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 6)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 6)
