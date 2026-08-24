"""The row-based transforms, kept exactly as they were.

Every one of these was deleted from `app/plugins/python_function/` when the
transform was rewritten to run on Arrow. They are kept here, verbatim, as the
oracle for that rewrite: the new implementation is not required to be a
plausible replacement, it is required to answer identically on a table built
from the cases that break naive ports - nulls, numeric text, mixed types,
duplicate rows, a column the parameters never mention.

Nothing imports this except the equivalence tests. When a transform's
behaviour is deliberately changed, the change belongs in a test that says so,
not in an edit to this file.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from app.shared.errors import ValidationError

_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)



def _as_number(value: Any) -> float | None:
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


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _require_column(rows: list[dict], column: str) -> None:
    if rows and column not in rows[0]:
        raise ValidationError(
            f"column '{column}' is not in the input",
            details={"available": sorted(rows[0])},
        )


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part) for part in (value or [])]


def _cast_types(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Force columns to a type, so later arithmetic and sorting behave.

    A source that reports a wind speed as text and a year as a float makes every
    downstream step defensive. Casting once, here, keeps them simple.
    """
    casts = params.get("casts") or {}
    if not isinstance(casts, dict) or not casts:
        raise ValidationError("cast_types needs a mapping of column to type")
    allowed = {"number", "integer", "text", "boolean"}
    unknown = sorted(set(casts.values()) - allowed)
    if unknown:
        raise ValidationError(
            f"unsupported target types: {unknown}", details={"allowed": sorted(allowed)}
        )

    out = []
    for row in rows:
        record = dict(row)
        for column, target in casts.items():
            raw = row.get(column)
            if target == "text":
                record[column] = None if raw is None else str(raw)
            elif target == "boolean":
                record[column] = (
                    None
                    if raw is None or str(raw).strip() == ""
                    else str(raw).strip().lower() in {"1", "true", "yes", "y", "是"}
                )
            else:
                number = _as_number(raw)
                record[column] = (
                    None
                    if number is None
                    else (int(number) if target == "integer" else number)
                )
        out.append(record)
    return out


def _fill_missing(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Replace nulls with a stated value, so a gap never reads as a zero."""
    columns = _as_list(params.get("columns"))
    replacement = params.get("value", 0)
    if not columns:
        raise ValidationError("fill_missing needs at least one column")
    out = []
    for row in rows:
        record = dict(row)
        for column in columns:
            value = record.get(column)
            if value is None or (isinstance(value, str) and not value.strip()):
                record[column] = replacement
        out.append(record)
    return out


def _datetime_parts(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Split a timestamp into the parts an analysis actually groups by.

    Seasonality is a question about months; trends are a question about years.
    Neither is answerable while the date is still one opaque string.
    """
    column = params["column"]
    _require_column(rows, column)
    prefix = params.get("prefix") or column
    wanted = _as_list(params.get("parts")) or ["year", "month", "day"]
    allowed = {"year", "month", "day", "hour", "dayofyear", "week", "quarter", "date"}
    unknown = sorted(set(wanted) - allowed)
    if unknown:
        raise ValidationError(
            f"unsupported parts: {unknown}", details={"allowed": sorted(allowed)}
        )

    out = []
    for row in rows:
        moment = _as_datetime(row.get(column))
        record = dict(row)
        for part in wanted:
            key = f"{prefix}_{part}"
            if moment is None:
                record[key] = None
            elif part == "year":
                record[key] = moment.year
            elif part == "month":
                record[key] = moment.month
            elif part == "day":
                record[key] = moment.day
            elif part == "hour":
                record[key] = moment.hour
            elif part == "dayofyear":
                record[key] = moment.timetuple().tm_yday
            elif part == "week":
                record[key] = moment.isocalendar()[1]
            elif part == "quarter":
                record[key] = (moment.month - 1) // 3 + 1
            else:
                record[key] = moment.strftime("%Y-%m-%d")
        out.append(record)
    return out


def _duration_between(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Hours (or days) between two timestamp columns."""
    start_column = params["start"]
    end_column = params["end"]
    unit = params.get("unit", "hours")
    if unit not in {"hours", "days", "minutes"}:
        raise ValidationError("unit must be hours, days or minutes")
    divisor = {"minutes": 60.0, "hours": 3600.0, "days": 86400.0}[unit]
    target = params.get("output") or f"duration_{unit}"

    out = []
    for row in rows:
        start = _as_datetime(row.get(start_column))
        end = _as_datetime(row.get(end_column))
        value = None
        if start and end and end >= start:
            value = round((end - start).total_seconds() / divisor, 3)
        out.append({**row, target: value})
    return out


def _bin_numeric(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Cut a numeric column into named bands.

    Bands are how a continuous measure becomes something you can group by, and
    naming them is the point: "severe" carries meaning that "48.2" does not.
    """
    column = params["column"]
    _require_column(rows, column)
    edges = [float(edge) for edge in (params.get("edges") or [])]
    labels = _as_list(params.get("labels"))
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

    out = []
    for row in rows:
        number = _as_number(row.get(column))
        out.append({**row, target: None if number is None else band(number)})
    return out


def _map_values(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Translate coded values into readable ones via a lookup table."""
    column = params["column"]
    mapping = params.get("mapping") or {}
    if not isinstance(mapping, dict) or not mapping:
        raise ValidationError("map_values needs a mapping")
    target = params.get("output") or f"{column}_label"
    fallback = params.get("default")
    keep_unmapped = bool(params.get("keep_unmapped", True))

    out = []
    for row in rows:
        key = row.get(column)
        text = "" if key is None else str(key)
        if text in mapping:
            value = mapping[text]
        elif fallback is not None:
            value = fallback
        elif keep_unmapped:
            value = key
        else:
            value = None
        out.append({**row, target: value})
    return out


def _extract_pattern(rows: list[dict], params: dict[str, Any]) -> list[dict]:
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

    out = []
    for row in rows:
        raw = row.get(column)
        value = None
        if isinstance(raw, str) and raw:
            match = pattern.search(raw[:2000])
            if match and group <= (match.re.groups or 0):
                value = match.group(group)
        out.append({**row, target: value})
    return out


def _flag_rows(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Mark rows matching a condition instead of dropping them.

    Filtering answers "which rows"; flagging keeps the denominator, which is
    what you need to state a rate rather than a count.
    """
    column = params["column"]
    operator = params.get("op", "not_empty")
    target_value = params.get("value")
    output = params.get("output") or f"{column}_flag"

    def matches(value: Any) -> bool:
        if operator == "not_empty":
            return value is not None and str(value).strip() != ""
        if operator == "is_empty":
            return value is None or str(value).strip() == ""
        if operator == "equals":
            return str(value) == str(target_value)
        if operator == "contains":
            return str(target_value) in str(value or "")
        if operator in {"gt", "gte", "lt", "lte"}:
            number = _as_number(value)
            bound = _as_number(target_value)
            if number is None or bound is None:
                return False
            return {
                "gt": number > bound,
                "gte": number >= bound,
                "lt": number < bound,
                "lte": number <= bound,
            }[operator]
        raise ValidationError(f"unsupported operator '{operator}'")

    return [{**row, output: matches(row.get(column))} for row in rows]


def _summarise(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Group by one or more columns and aggregate several measures at once.

    The single-measure `group_aggregate` is fine for one number; a real summary
    table wants a count beside a mean beside a max, in one pass.
    """
    group_by = _as_list(params.get("group_by"))
    measures = params.get("measures") or {}
    if not isinstance(measures, dict):
        raise ValidationError("measures must map a column to an aggregation")
    allowed = {"sum", "mean", "min", "max", "count", "median"}
    unknown = sorted({str(v) for v in measures.values()} - allowed)
    if unknown:
        raise ValidationError(
            f"unsupported aggregations: {unknown}", details={"allowed": sorted(allowed)}
        )

    buckets: dict[tuple, dict[str, list[float]]] = {}
    sizes: dict[tuple, int] = {}
    keys: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(column) for column in group_by)
        keys.setdefault(key, {column: row.get(column) for column in group_by})
        sizes[key] = sizes.get(key, 0) + 1
        bucket = buckets.setdefault(key, {column: [] for column in measures})
        for column in measures:
            number = _as_number(row.get(column))
            if number is not None:
                bucket[column].append(number)

    out = []
    for key, values in buckets.items():
        record = dict(keys[key])
        record["row_count"] = sizes[key]
        for column, how in measures.items():
            record[f"{column}_{how}"] = _aggregate(values[column], str(how), sizes[key])
        out.append(record)
    return sorted(out, key=lambda r: tuple(str(r.get(c)) for c in group_by))


def _aggregate(values: list[float], how: str, size: int) -> float | None:
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


def _rank_rows(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Add a 1-based rank over a numeric column, highest first by default."""
    column = params["column"]
    _require_column(rows, column)
    descending = bool(params.get("descending", True))
    target = params.get("output") or f"{column}_rank"
    ordered = sorted(
        [row for row in rows if _as_number(row.get(column)) is not None],
        key=lambda row: _as_number(row.get(column)) or 0.0,
        reverse=descending,
    )
    ranks = {id(row): index + 1 for index, row in enumerate(ordered)}
    return [{**row, target: ranks.get(id(row))} for row in rows]


def _percent_of_total(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Express a column as a share of its own total."""
    column = params["column"]
    _require_column(rows, column)
    target = params.get("output") or f"{column}_pct"
    total = sum(v for v in (_as_number(row.get(column)) for row in rows) if v is not None)
    if not total:
        raise ValidationError(f"column '{column}' sums to zero, so shares are undefined")
    out = []
    for row in rows:
        number = _as_number(row.get(column))
        share = None if number is None else round(100 * number / total, 4)
        out.append({**row, target: share})
    return out


def _numbers(rows: list[dict], column: str) -> list[float]:
    values = []
    for row in rows:
        value = row.get(column)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not (isinstance(value, float) and math.isnan(value)):
                values.append(float(value))
    return values


def _moving_average(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    column = params["column"]
    window = int(params.get("window", 3))
    target = params.get("output") or f"{column}_ma{window}"
    if window < 1:
        raise ValidationError("window must be >= 1")
    out: list[dict] = []
    buffer: list[float] = []
    for row in rows:
        value = row.get(column)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            buffer.append(float(value))
        if len(buffer) > window:
            buffer.pop(0)
        average = round(sum(buffer) / len(buffer), 6) if buffer else None
        out.append({**row, target: average})
    return out


def _zscore_outliers(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    column = params["column"]
    threshold = float(params.get("threshold", 3.0))
    values = _numbers(rows, column)
    if len(values) < 2:
        raise ValidationError(f"column '{column}' has too few numeric values")
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    stddev = math.sqrt(variance) or 1e-12
    out = []
    for row in rows:
        value = row.get(column)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            score = (float(value) - mean) / stddev
            out.append({**row, "zscore": round(score, 6),
                        "is_outlier": abs(score) > threshold})
        else:
            out.append({**row, "zscore": None, "is_outlier": None})
    return out


def _group_aggregate(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    by = params["group_by"]
    column = params["column"]
    how = params.get("agg", "sum")
    buckets: dict[Any, list[float]] = {}
    for row in rows:
        key = row.get(by)
        value = row.get(column)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            buckets.setdefault(key, []).append(float(value))
    out = []
    for key, values in buckets.items():
        if how == "sum":
            aggregated = sum(values)
        elif how == "mean":
            aggregated = sum(values) / len(values)
        elif how == "min":
            aggregated = min(values)
        elif how == "max":
            aggregated = max(values)
        elif how == "count":
            aggregated = len(values)
        else:
            raise ValidationError(f"unsupported aggregation '{how}'")
        out.append({by: key, f"{column}_{how}": round(aggregated, 6), "count": len(values)})
    return sorted(out, key=lambda r: str(r[by]))


def _parse_numeric(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Pull the leading number out of a text column.

    Real sources carry values like "30 (m/s)", "1,013" or a localised "no data"
    string in the same column. This lifts the number out into a clean numeric
    column and leaves the rest null, so downstream steps can do arithmetic.
    """
    import re

    column = params["column"]
    target = params.get("output") or f"{column}_value"
    keep_text = bool(params.get("keep_original", True))
    pattern = re.compile(r"-?\d+(?:[\d,]*\d)?(?:\.\d+)?")

    out: list[dict] = []
    for row in rows:
        raw = row.get(column)
        value: float | None = None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
        elif isinstance(raw, str):
            match = pattern.search(raw.replace(",", ""))
            if match:
                try:
                    value = float(match.group())
                except ValueError:
                    value = None
        record = dict(row) if keep_text else {k: v for k, v in row.items() if k != column}
        record[target] = value
        out.append(record)
    return out


def _filter_rows(rows: list[dict], params: dict[str, Any]) -> list[dict]:
    """Keep the rows worth carrying forward."""
    column = params["column"]
    operator = params.get("op", "not_empty")
    target = params.get("value")

    def keep(row: dict) -> bool:
        value = row.get(column)
        if operator == "not_empty":
            return value is not None and str(value).strip() != ""
        if operator == "is_empty":
            return value is None or str(value).strip() == ""
        if operator == "equals":
            return str(value) == str(target)
        if operator == "not_equals":
            return str(value) != str(target)
        if operator in ("gt", "gte", "lt", "lte"):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
            bound = float(target)
            return {
                "gt": value > bound,
                "gte": value >= bound,
                "lt": value < bound,
                "lte": value <= bound,
            }[operator]
        if operator == "in":
            return str(value) in {str(v) for v in (target or [])}
        raise ValidationError(f"unsupported filter operator '{operator}'")

    kept = [row for row in rows if keep(row)]
    if not kept:
        raise ValidationError(
            f"the filter on '{column}' removed every row, so there is nothing "
            f"for the next step to read"
        )
    return kept
