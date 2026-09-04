"""Reshaping and time-series transforms, written against `Table`.

The twenty-two standard transforms cover cleaning, deriving and aggregating a
table whose rows are already the unit of analysis. They cover nothing at all
about a table whose rows are *observations over time*, and that gap is not a
niche one: a measurement store is long (one row per reading), an analysis table
is wide (one row per subject per period), and there was no verb that turned one
into the other.

What is here is the vocabulary a condition-monitoring, capacity-planning or
cohort analysis needs, and none of it names a domain:

    pivot_wider      long readings  -> one column per measurement
    unpivot_longer   wide columns   -> long readings
    resample_time    irregular time -> fixed periods
    rolling_stats    a trailing window, per group, in time order
    linear_trend     one row per group: slope, R^2, change over the window
    rate_of_change   how fast a value is moving, per hour or per day
    lag_column       what this series read N periods ago
    correlation      whether two measurements move together

Three properties are shared by all of them and are the reason they compose:

* **Grouping is explicit.** A trailing mean over a table holding forty assets'
  readings is meaningless unless it restarts at each asset. Every windowed
  transform takes `group_by`, and the existing `moving_average` - which does
  not - is left exactly as it was rather than quietly changed underneath the
  pipelines that use it.
* **Order is explicit.** Time series arrive in whatever order the store
  returned them. Each transform sorts within the group by `order_by` before
  it computes anything, and writes its answer back to the row it came from,
  so the caller's row order is preserved.
* **Columns in, columns out.** Nothing here calls `to_rows()`.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from app.shared.errors import ValidationError
from app.shared.tabular import Table

from .columnar import as_datetime, as_list, as_number, require_column, sort_key

# --------------------------------------------------------------------------
# shared machinery
# --------------------------------------------------------------------------
#  Every windowed transform answers the same two questions first: which rows
#  belong together, and in what order do they read. Answering them once here is
#  what keeps six transforms from each having their own idea of a group.


def _group_keys(table: Table, group_by: list[str]) -> list[tuple]:
    """One hashable key per row. An empty grouping puts everything together."""
    if not group_by:
        return [()] * table.num_rows
    columns = [table.column_values(name) for name in group_by]
    return [tuple(str(col[i]) for col in columns) for i in range(table.num_rows)]


def _ordering(table: Table, order_by: str | None) -> list[Any]:
    """The value each row is ordered by within its group."""
    if not order_by:
        #  No ordering column means "the order the rows arrived in", which is
        #  what a store that already returned them sorted gives you.
        return list(range(table.num_rows))
    require_column(table, order_by)
    return [_orderable(v) for v in table.column_values(order_by)]


def _orderable(value: Any):
    """A sortable stand-in for a value that may be a time, a number or text."""
    moment = as_datetime(value)
    if moment is not None:
        return (0, moment.timestamp(), "")
    return sort_key(value)


def _seconds(value: Any) -> float | None:
    """A value as a point on the time axis, in seconds, if it is one at all."""
    moment = as_datetime(value)
    if moment is not None:
        return moment.timestamp()
    number = as_number(value)
    return None if number is None else float(number)


def _segments(table: Table, group_by: list[str], order_by: str | None) -> list[list[int]]:
    """Row positions, grouped and then ordered inside each group.

    Returned as positions rather than as sub-tables so that a transform can
    write its answer straight back into a column of the original length: the
    caller's row order is theirs, not something a rolling mean gets to change.
    """
    for name in group_by:
        require_column(table, name)
    keys = _group_keys(table, group_by)
    order = _ordering(table, order_by)

    buckets: dict[tuple, list[int]] = {}
    for index, key in enumerate(keys):
        buckets.setdefault(key, []).append(index)
    #  Stable, so rows that tie on the ordering column keep their arrival order.
    return [sorted(rows, key=lambda i: order[i]) for rows in buckets.values()]


def _ordered_groups(
    table: Table, group_by: list[str], order_by: str | None
) -> list[tuple[dict[str, Any], list[int]]]:
    """The same segments, each paired with the identity of its group.

    Aggregating transforms need to name the group they answer for; windowed
    ones do not. Keeping the identity here rather than reconstructing it from
    the first row of each segment means an aggregate reports the group's own
    values, not whichever row happened to sort first.
    """
    for name in group_by:
        require_column(table, name)
    columns = {name: table.column_values(name) for name in group_by}
    keys = _group_keys(table, group_by)
    order = _ordering(table, order_by)

    buckets: dict[tuple, list[int]] = {}
    identities: dict[tuple, dict[str, Any]] = {}
    for index, key in enumerate(keys):
        buckets.setdefault(key, []).append(index)
        identities.setdefault(key, {n: columns[n][index] for n in group_by})
    return [
        (identities[key], sorted(rows, key=lambda i: order[i]))
        for key, rows in buckets.items()
    ]


def _numbers(table: Table, column: str) -> list[float | None]:
    require_column(table, column)
    return [as_number(v) for v in table.column_values(column)]


# --------------------------------------------------------------------------
# statistics over a window
# --------------------------------------------------------------------------
ROLLING_STATISTICS = (
    "mean", "std", "median", "min", "max", "sum", "count", "slope", "zscore",
    "range", "r_squared",
)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _stdev(values: list[float]) -> float | None:
    """Sample standard deviation. One observation has no spread, not zero."""
    if len(values) < 2:
        return None
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _slope(xs: list[float], ys: list[float]) -> float | None:
    """Least-squares slope of y on x, or None when x does not vary."""
    if len(xs) < 2:
        return None
    mean_x, mean_y = _mean(xs), _mean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    return numerator / denominator


def _r_squared(
    xs: list[float], ys: list[float], slope: float, intercept: float
) -> float | None:
    """How much of the movement the straight line accounts for."""
    total = sum((y - _mean(ys)) ** 2 for y in ys)
    if total <= 0:
        #  A flat series is perfectly described by a flat line, but calling
        #  that R^2 = 1 would let a stuck sensor claim the strongest trend in
        #  the fleet. Undefined is the honest answer.
        return None
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys, strict=False))
    return max(0.0, 1.0 - residual / total)


def _window_statistic(how: str, xs: list[float], ys: list[float]) -> float | None:
    if not ys:
        return None
    if how == "count":
        return float(len(ys))
    if how == "mean":
        return _mean(ys)
    if how == "sum":
        return sum(ys)
    if how == "min":
        return min(ys)
    if how == "max":
        return max(ys)
    if how == "range":
        return max(ys) - min(ys)
    if how == "median":
        return _median(ys)
    if how == "std":
        return _stdev(ys)
    if how == "slope":
        return _slope(xs, ys)
    if how == "r_squared":
        #  Reported beside the slope, because a slope without it cannot be
        #  told apart from a line drawn through noise - and acting on the
        #  second is how a condition-monitoring system loses its audience.
        slope = _slope(xs, ys)
        if slope is None:
            return None
        intercept = _mean(ys) - slope * _mean(xs)
        return _r_squared(xs, ys, slope, intercept)
    if how == "zscore":
        spread = _stdev(ys)
        if not spread:
            return None
        return (ys[-1] - _mean(ys)) / spread
    raise ValidationError(f"unknown rolling statistic '{how}'")


# --------------------------------------------------------------------------
# reshaping
# --------------------------------------------------------------------------
PIVOT_AGGREGATIONS = ("mean", "sum", "min", "max", "median", "count", "first", "last")


def _collapse(values: list[Any], how: str) -> Any:
    """Reduce the values that landed in one cell to the one the cell shows."""
    if not values:
        return None
    if how == "first":
        return values[0]
    if how == "last":
        return values[-1]
    if how == "count":
        return float(len(values))
    numbers = [n for n in (as_number(v) for v in values) if n is not None]
    if not numbers:
        #  A non-numeric column cannot be averaged, and refusing the whole
        #  pivot over it would make a text measurement unpivotable. The last
        #  value is what a wide view of text actually wants.
        return values[-1]
    if how == "mean":
        return round(_mean(numbers), 6)
    if how == "sum":
        return round(sum(numbers), 6)
    if how == "min":
        return min(numbers)
    if how == "max":
        return max(numbers)
    if how == "median":
        return round(_median(numbers), 6)
    raise ValidationError(f"unknown aggregation '{how}'")


def pivot_wider(table: Table, params: dict[str, Any]) -> Table:
    """Long readings in, one column per measurement out.

    This is the transform that makes a measurement store analysable. A
    historian answers `(timestamp, asset, parameter, value)`; every question
    anybody asks of it - is temperature high *while* load is low, do vibration
    and bearing temperature move together - needs those on one row.

    Cells are aggregated rather than assumed unique, because a duplicated
    reading is a fact of every real store and silently keeping one of the two
    would make the answer depend on file order.
    """
    keys = as_list(params.get("keys"))
    name_column = params.get("name_from")
    value_column = params.get("value_from")
    if not keys:
        raise ValidationError("pivot_wider needs at least one key column")
    if not name_column or not value_column:
        raise ValidationError("pivot_wider needs 'name_from' and 'value_from'")
    how = str(params.get("aggregation", "mean"))
    if how not in PIVOT_AGGREGATIONS:
        raise ValidationError(
            f"unknown aggregation '{how}'", details={"allowed": list(PIVOT_AGGREGATIONS)}
        )
    prefix = str(params.get("prefix") or "")
    for column in (*keys, name_column, value_column):
        require_column(table, column)

    key_columns = [table.column_values(name) for name in keys]
    names = table.column_values(name_column)
    values = table.column_values(value_column)

    #  Column order follows first appearance, not the alphabet: a pivot of
    #  ordered measurements should not come back reordered by accident.
    seen: dict[str, None] = {}
    cells: dict[tuple, dict[str, list[Any]]] = {}
    identities: dict[tuple, list[Any]] = {}
    order: list[tuple] = []
    for index in range(table.num_rows):
        name = names[index]
        if name is None or str(name) == "":
            continue
        label = str(name)
        seen.setdefault(label, None)
        key = tuple(str(col[index]) for col in key_columns)
        if key not in cells:
            cells[key] = {}
            identities[key] = [col[index] for col in key_columns]
            order.append(key)
        cells[key].setdefault(label, []).append(values[index])

    if not seen:
        raise ValidationError(
            f"column '{name_column}' holds no values to pivot into columns"
        )
    labels = [f"{prefix}{label}" for label in seen]
    columns: dict[str, list[Any]] = {
        name: [identities[key][at] for key in order] for at, name in enumerate(keys)
    }
    for label, output in zip(seen, labels, strict=False):
        columns[output] = [_collapse(cells[key].get(label, []), how) for key in order]
    return Table.from_columns(columns)


def unpivot_longer(table: Table, params: dict[str, Any]) -> Table:
    """Wide columns in, long readings out - the inverse of `pivot_wider`.

    Needed as often as the pivot is: a spreadsheet arrives with a column per
    month, and every grouping verb in the vocabulary wants those as rows.
    """
    id_columns = as_list(params.get("keys"))
    measures = as_list(params.get("columns"))
    name_output = str(params.get("name_to") or "measurement")
    value_output = str(params.get("value_to") or "value")
    drop_null = bool(params.get("drop_missing", True))

    if not measures:
        #  Everything that is not an identifier is a measurement, which is what
        #  a wide export normally means.
        measures = [c for c in table.columns if c not in id_columns]
    if not measures:
        raise ValidationError("unpivot_longer needs at least one column to melt")
    for column in (*id_columns, *measures):
        require_column(table, column)

    identity = {name: table.column_values(name) for name in id_columns}
    melted = {name: table.column_values(name) for name in measures}

    columns: dict[str, list[Any]] = {name: [] for name in id_columns}
    columns[name_output] = []
    columns[value_output] = []
    for index in range(table.num_rows):
        for measure in measures:
            value = melted[measure][index]
            if drop_null and value is None:
                continue
            for name in id_columns:
                columns[name].append(identity[name][index])
            columns[name_output].append(measure)
            columns[value_output].append(value)
    return Table.from_columns(columns)


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------
_PERIODS = ("hour", "day", "week", "month", "quarter", "year")
RESAMPLE_MEASURES = (
    "mean", "sum", "min", "max", "median", "count", "first", "last", "std",
)


def _iso_bucket(value: Any, period: str) -> str | None:
    """The period label straight off an ISO-shaped string, or None.

    Timestamps reach a transform as `Table.column_values` renders them, which
    is ISO text. Parsing 400,000 of those back into datetimes in order to
    format them again is most of what resampling used to cost, and for the
    three periods whose label is a prefix of the string it is entirely
    avoidable. Anything else - a week, a quarter, a different format - falls
    through to the general path.
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    if value[4] != "-" or value[7] != "-":
        return None
    if period == "day":
        return value[:10]
    if period == "month":
        return value[:8] + "01"
    if period == "year":
        return value[:4] + "-01-01"
    if period == "hour" and len(value) >= 13:
        return f"{value[:13]}:00:00"
    return None


def _bucket(moment: datetime, period: str) -> str:
    """The label of the period a moment falls in, as an ISO-ordered string."""
    if period == "hour":
        return moment.strftime("%Y-%m-%d %H:00:00")
    if period == "day":
        return moment.strftime("%Y-%m-%d")
    if period == "week":
        monday = moment.date() - timedelta(days=moment.weekday())
        return monday.strftime("%Y-%m-%d")
    if period == "month":
        return moment.strftime("%Y-%m-01")
    if period == "quarter":
        first = 3 * ((moment.month - 1) // 3) + 1
        return f"{moment.year:04d}-{first:02d}-01"
    return f"{moment.year:04d}-01-01"


def _measure(values: list[Any], numbers: list[float], how: str) -> Any:
    """One aggregation, over values already parsed once by the caller.

    Reducing `numbers` directly rather than handing them back to `_collapse`
    is not a micro-optimisation: `_collapse` parses whatever it is given, so
    routing an already-parsed list through it re-parsed every value once per
    aggregation, and a table asking for four statistics of one column parsed
    it four more times.
    """
    if how in ("first", "last", "count"):
        return _collapse(values, how)
    if not numbers:
        return None
    if how == "std":
        spread = _stdev(numbers)
        return None if spread is None else round(spread, 6)
    if how == "mean":
        return round(_mean(numbers), 6)
    if how == "sum":
        return round(sum(numbers), 6)
    if how == "min":
        return min(numbers)
    if how == "max":
        return max(numbers)
    if how == "median":
        return round(_median(numbers), 6)
    raise ValidationError(f"unknown aggregation '{how}'")


def _measure_plan(raw: Any) -> dict[str, list[str]]:
    """Read `measures`, which may ask for one aggregation or several.

    One column, several statistics is the normal case for a measurement -
    a mean beside a peak beside a spread - and a mapping cannot repeat a key.
    So a value may be a list, and a bare string means a list of one.
    """
    if not isinstance(raw, dict) or not raw:
        raise ValidationError("resample_time needs 'measures': column -> aggregation")
    plan: dict[str, list[str]] = {}
    for column, wanted in raw.items():
        hows = (
            [str(w) for w in wanted]
            if isinstance(wanted, (list, tuple))
            else [str(wanted)]
        )
        if not hows:
            raise ValidationError(f"'{column}' names no aggregation")
        unknown = sorted(set(hows) - set(RESAMPLE_MEASURES))
        if unknown:
            raise ValidationError(
                f"unsupported aggregations: {unknown}",
                details={"allowed": list(RESAMPLE_MEASURES)},
            )
        plan[str(column)] = list(dict.fromkeys(hows))
    return plan


def resample_time(table: Table, params: dict[str, Any]) -> Table:
    """Collapse irregular readings onto fixed periods.

    Sampling intervals are never as regular as a schema claims: a store drops
    readings, a device reconnects and back-fills, two sources disagree by
    ninety seconds. Comparing two series that were not sampled at the same
    moments is the quiet mistake underneath most time-series analysis, and the
    fix is to put both on a grid first.
    """
    column = params.get("timestamp")
    if not column:
        raise ValidationError("resample_time needs a 'timestamp' column")
    require_column(table, column)
    period = str(params.get("period", "day"))
    if period not in _PERIODS:
        raise ValidationError(
            f"unknown period '{period}'", details={"allowed": list(_PERIODS)}
        )
    measures = _measure_plan(params.get("measures"))
    group_by = as_list(params.get("group_by"))
    output = str(params.get("output") or "period")

    for name in (*group_by, *measures):
        require_column(table, name)

    stamps = table.column_values(column)
    buckets: list[str | None] = []
    for value in stamps:
        fast = _iso_bucket(value, period)
        if fast is not None:
            buckets.append(fast)
            continue
        moment = as_datetime(value) or (
            value if isinstance(value, (datetime, date)) else None
        )
        if isinstance(moment, date) and not isinstance(moment, datetime):
            moment = datetime(moment.year, moment.month, moment.day)
        buckets.append(_bucket(moment, period) if moment is not None else None)

    key_columns = [table.column_values(name) for name in group_by]
    sources = [(name, table.column_values(name)) for name in measures]

    order: list[tuple] = []
    identities: dict[tuple, list[Any]] = {}
    collected: dict[tuple, list[list[Any]]] = {}
    sizes: dict[tuple, int] = {}
    #  Measures are held positionally rather than by name: one dict lookup per
    #  measure per row is a million dictionary probes on a table this size, and
    #  the names are already fixed before the loop starts.
    width = len(sources)
    for index in range(table.num_rows):
        bucket = buckets[index]
        if bucket is None:
            #  A reading whose timestamp cannot be read belongs to no period.
            #  Dropping it here is right; the data-quality layer is where it
            #  gets counted.
            continue
        key = (*(col[index] for col in key_columns), bucket)
        bucketed = collected.get(key)
        if bucketed is None:
            bucketed = collected[key] = [[] for _ in range(width)]
            identities[key] = [col[index] for col in key_columns]
            order.append(key)
            sizes[key] = 0
        sizes[key] += 1
        for at in range(width):
            bucketed[at].append(sources[at][1][index])

    order.sort(key=lambda key: tuple(str(part) for part in key))
    columns: dict[str, list[Any]] = {
        name: [identities[key][at] for key in order] for at, name in enumerate(group_by)
    }
    columns[output] = [key[-1] for key in order]
    columns["sample_count"] = [sizes[key] for key in order]
    for at, (name, _) in enumerate(sources):
        hows = measures[name]
        answers: dict[str, list[Any]] = {how: [] for how in hows}
        for key in order:
            values = collected[key][at]
            #  Parsed once per group, then every aggregation of that column
            #  reads the same list. Re-parsing per aggregation made a table
            #  asking for mean, max, min and spread cost four passes over the
            #  same values, which was most of the run time.
            numbers = [n for n in (as_number(v) for v in values) if n is not None]
            for how in hows:
                answers[how].append(_measure(values, numbers, how))
        for how in hows:
            columns[f"{name}_{how}"] = answers[how]
    return Table.from_columns(columns)


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------
#  Seconds per unit. Every transform that reports a rate names one of these,
#  so "per day" means the same thing in a rolling slope, a rate of change and
#  a fitted trend.
_RATE_UNITS = {"second": 1.0, "minute": 60.0, "hour": 3600.0, "day": 86400.0}


def rolling_stats(table: Table, params: dict[str, Any]) -> Table:
    """A trailing window over each group, in time order.

    `moving_average` already does the mean over a whole table. This does any of
    ten statistics, restarts at each group and sorts by time first - and it is
    a separate transform rather than an extension of that one, because a
    pipeline in the field depends on exactly what the old one answers.

    `slope` is the one worth naming: a value still inside its limits but rising
    steadily for five windows is the case a threshold cannot see, and it is the
    reason condition monitoring exists at all.
    """
    column = params.get("column")
    if not column:
        raise ValidationError("rolling_stats needs a 'column'")
    window = int(params.get("window", 7))
    if window < 2:
        raise ValidationError("window must be >= 2")
    statistics = as_list(params.get("statistics")) or ["mean"]
    unknown = sorted(set(statistics) - set(ROLLING_STATISTICS))
    if unknown:
        raise ValidationError(
            f"unknown rolling statistics: {unknown}",
            details={"allowed": list(ROLLING_STATISTICS)},
        )
    minimum = int(params.get("min_periods", 2))
    group_by = as_list(params.get("group_by"))
    order_by = params.get("order_by")
    prefix = str(params.get("prefix") or f"{column}_roll")

    values = _numbers(table, column)
    #  `slope` needs a real x axis; everything else only needs the order.
    axis = (
        [_seconds(v) for v in table.column_values(order_by)]
        if order_by and "slope" in statistics
        else [float(i) for i in range(table.num_rows)]
    )
    axis = [float(i) if x is None else x for i, x in enumerate(axis)]
    #  Slope per unit time rather than per sample: a per-sample slope means
    #  nothing once the sampling interval changes, which is the case this
    #  exists for. Which unit is the caller's to choose - per hour reads well
    #  for a sensor, per day for a daily aggregate - and getting it wrong
    #  produces a number that is right and unreadable.
    unit = str(params.get("per", "hour"))
    if unit not in _RATE_UNITS:
        raise ValidationError(
            f"unknown unit '{unit}'", details={"allowed": sorted(_RATE_UNITS)}
        )
    scale = _RATE_UNITS[unit] if (order_by and "slope" in statistics) else 1.0

    outputs: dict[str, list[float | None]] = {
        how: [None] * table.num_rows for how in statistics
    }
    for rows in _segments(table, group_by, order_by):
        window_x: list[float] = []
        window_y: list[float] = []
        for position in rows:
            value = values[position]
            if value is not None:
                window_x.append(axis[position])
                window_y.append(value)
            if len(window_y) > window:
                window_x.pop(0)
                window_y.pop(0)
            if len(window_y) < minimum:
                continue
            for how in statistics:
                answer = _window_statistic(how, window_x, window_y)
                if answer is not None and how == "slope":
                    answer *= scale
                outputs[how][position] = (
                    None if answer is None else round(float(answer), 6)
                )

    result = table
    for how in statistics:
        result = result.set_column(f"{prefix}_{how}{window}", outputs[how])
    return result


def lag_column(table: Table, params: dict[str, Any]) -> Table:
    """What this series read N periods ago, per group and in time order."""
    column = params.get("column")
    if not column:
        raise ValidationError("lag_column needs a 'column'")
    require_column(table, column)
    periods = int(params.get("periods", 1))
    if periods == 0:
        raise ValidationError("periods must not be zero")
    group_by = as_list(params.get("group_by"))
    order_by = params.get("order_by")
    direction = "lead" if periods < 0 else "lag"
    target = params.get("output") or f"{column}_{direction}{abs(periods)}"

    values = table.column_values(column)
    shifted: list[Any] = [None] * table.num_rows
    for rows in _segments(table, group_by, order_by):
        for at, position in enumerate(rows):
            source = at - periods
            if 0 <= source < len(rows):
                shifted[position] = values[rows[source]]
    return table.set_column(target, shifted)


def rate_of_change(table: Table, params: dict[str, Any]) -> Table:
    """How fast a value is moving: delta value over delta time.

    Reported per hour or per day rather than per sample, because a rate that
    depends on the sampling interval is not comparable between two assets - and
    comparing them is the whole point of measuring it.
    """
    column = params.get("column")
    if not column:
        raise ValidationError("rate_of_change needs a 'column'")
    periods = int(params.get("periods", 1))
    if periods < 1:
        raise ValidationError("periods must be >= 1")
    unit = str(params.get("per", "hour"))
    if unit not in _RATE_UNITS:
        raise ValidationError(
            f"unknown unit '{unit}'", details={"allowed": sorted(_RATE_UNITS)}
        )
    group_by = as_list(params.get("group_by"))
    order_by = params.get("order_by")
    target = params.get("output") or f"{column}_per_{unit}"

    values = _numbers(table, column)
    axis = (
        [_seconds(v) for v in table.column_values(order_by)]
        if order_by
        else [float(i) * _RATE_UNITS[unit] for i in range(table.num_rows)]
    )
    rates: list[float | None] = [None] * table.num_rows
    for rows in _segments(table, group_by, order_by):
        for at in range(periods, len(rows)):
            here, there = rows[at], rows[at - periods]
            y1, y0 = values[here], values[there]
            x1, x0 = axis[here], axis[there]
            if None in (y1, y0, x1, x0):
                continue
            elapsed = (x1 - x0) / _RATE_UNITS[unit]
            if elapsed <= 0:
                continue
            rates[here] = round((y1 - y0) / elapsed, 6)
    return table.set_column(target, rates)


# --------------------------------------------------------------------------
# aggregating: one row per group
# --------------------------------------------------------------------------
def linear_trend(table: Table, params: dict[str, Any]) -> Table:
    """Fit a straight line per group and report what it says.

    An answer per group rather than per row, because "is this rising" is a
    property of a series, not of a reading. The output states the slope in
    units per hour or per day, how much of the movement the line accounts for
    (R^2), and the plain first-to-last change - so a reader can see when a
    confident-looking slope is fitted through noise.
    """
    column = params.get("column")
    if not column:
        raise ValidationError("linear_trend needs a 'column'")
    group_by = as_list(params.get("group_by"))
    order_by = params.get("order_by")
    unit = str(params.get("per", "hour"))
    if unit not in _RATE_UNITS:
        raise ValidationError(
            f"unknown unit '{unit}'", details={"allowed": sorted(_RATE_UNITS)}
        )
    minimum = int(params.get("min_periods", 3))
    prefix = str(params.get("prefix") or column)

    values = _numbers(table, column)
    axis = (
        [_seconds(v) for v in table.column_values(order_by)]
        if order_by
        else [float(i) * _RATE_UNITS[unit] for i in range(table.num_rows)]
    )
    stamps = table.column_values(order_by) if order_by else [None] * table.num_rows

    columns: dict[str, list[Any]] = {name: [] for name in group_by}
    for name in (
        "points", "slope_per_" + unit, "intercept", "r_squared",
        "first_value", "last_value", "change", "change_pct", "direction",
        "first_seen", "last_seen",
    ):
        columns[f"{prefix}_{name}" if name not in group_by else name] = []

    for identity, rows in _ordered_groups(table, group_by, order_by):
        usable = [i for i in rows if values[i] is not None and axis[i] is not None]
        #  x is measured from the group's own first observation, so the
        #  intercept reads as "the fitted value where this window starts".
        #  Against a raw epoch it is the fitted value in 1970, which is a
        #  number nobody can check and everybody would have to ignore.
        origin = axis[usable[0]] if usable else 0.0
        xs = [(axis[i] - origin) / _RATE_UNITS[unit] for i in usable]
        ys = [values[i] for i in usable]
        slope = _slope(xs, ys) if len(usable) >= minimum else None
        intercept = (_mean(ys) - slope * _mean(xs)) if slope is not None else None
        r2 = (
            _r_squared(xs, ys, slope, intercept)
            if slope is not None and intercept is not None
            else None
        )
        first = ys[0] if ys else None
        last = ys[-1] if ys else None
        change = None if first is None or last is None else last - first
        change_pct = (
            None if change is None or not first else round(100 * change / abs(first), 4)
        )
        #  A direction nobody can act on should say so rather than round to
        #  "stable": that is the difference between a flat series and one whose
        #  window was too short to tell.
        if slope is None:
            direction = "unknown"
        elif r2 is not None and r2 < 0.2:
            direction = "unstable"
        elif abs(change_pct or 0) < 2:
            direction = "stable"
        else:
            direction = "increasing" if slope > 0 else "decreasing"

        for name, value in identity.items():
            columns[name].append(value)
        answers = {
            "points": len(usable),
            f"slope_per_{unit}": None if slope is None else round(slope, 8),
            "intercept": None if intercept is None else round(intercept, 6),
            "r_squared": None if r2 is None else round(r2, 6),
            "first_value": first,
            "last_value": last,
            "change": None if change is None else round(change, 6),
            "change_pct": change_pct,
            "direction": direction,
            "first_seen": stamps[usable[0]] if usable else None,
            "last_seen": stamps[usable[-1]] if usable else None,
        }
        for name, value in answers.items():
            columns[f"{prefix}_{name}"].append(value)

    return Table.from_columns(columns)


def correlation(table: Table, params: dict[str, Any]) -> Table:
    """Whether two measurements move together, per group.

    Evidence, not a conclusion. Two signals rising together is what turns "one
    reading looks odd" into "the thing they are both attached to is changing",
    and it is also exactly what a shared sensor fault looks like - so the count
    is reported beside the coefficient rather than left implicit.
    """
    columns = as_list(params.get("columns"))
    if len(columns) < 2:
        raise ValidationError("correlation needs at least two columns")
    group_by = as_list(params.get("group_by"))
    minimum = int(params.get("min_periods", 5))
    for name in columns:
        require_column(table, name)

    series = {name: _numbers(table, name) for name in columns}
    pairs = [
        (columns[i], columns[j])
        for i in range(len(columns))
        for j in range(i + 1, len(columns))
    ]

    out: dict[str, list[Any]] = {name: [] for name in group_by}
    out.update({"measurement_a": [], "measurement_b": [], "correlation": [], "points": []})
    for identity, rows in _ordered_groups(table, group_by, None):
        for left, right in pairs:
            xs, ys = [], []
            for index in rows:
                a, b = series[left][index], series[right][index]
                if a is not None and b is not None:
                    xs.append(a)
                    ys.append(b)
            for name, value in identity.items():
                out[name].append(value)
            out["measurement_a"].append(left)
            out["measurement_b"].append(right)
            out["points"].append(len(xs))
            out["correlation"].append(
                None if len(xs) < minimum else _pearson(xs, ys)
            )
    return Table.from_columns(out)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    mean_x, mean_y = _mean(xs), _mean(ys)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denominator = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denominator <= 0:
        #  One of the two never moved. There is no relationship to report, and
        #  reporting zero would read as "measured, and unrelated".
        return None
    return round(sum(a * b for a, b in zip(dx, dy, strict=False)) / denominator, 6)
