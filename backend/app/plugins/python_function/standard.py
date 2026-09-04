"""The standard transform set: the vocabulary a pipeline is written in.

Every transform here takes a table and returns a table, names its parameters in
a Contract, and does exactly one thing. That is what makes a pipeline free:
steps compose because they all speak the same shape, so a raw file can be
reshaped into an analysis table without anyone writing code for the occasion.

The transforms in `library.py` came first and stay where they are; this module
adds the general-purpose reshaping vocabulary and registers it into the same
registry.
"""

from __future__ import annotations

from typing import Any

from app.shared.contracts import Contract, ContractShape, FieldSpec, FieldType

from . import columnar, timeseries
from .library import Transform, register

# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part) for part in (value or [])]


# --------------------------------------------------------------------------
# reshaping
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# deriving
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------
def _object(*fields: FieldSpec) -> Contract:
    return Contract(shape=ContractShape.OBJECT, fields=list(fields))


_STANDARD = [
    Transform(
        key="rename_columns",
        name="Rename columns",
        description="Renames columns via an old → new mapping. Untouched columns stay.",
        table_fn=columnar.rename_columns,
        parameters=_object(
            FieldSpec("mapping", FieldType.JSON, description="old name → new name"),
        ),
    ),
    Transform(
        key="drop_columns",
        name="Drop columns",
        description="Removes the listed columns and keeps everything else.",
        table_fn=columnar.drop_columns,
        parameters=_object(FieldSpec("columns", FieldType.ARRAY)),
    ),
    Transform(
        key="cast_types",
        name="Cast types",
        description="Forces columns to number, integer, text or boolean.",
        table_fn=columnar.cast_types,
        parameters=_object(
            FieldSpec("casts", FieldType.JSON,
                      description="column → number | integer | text | boolean"),
        ),
    ),
    Transform(
        key="fill_missing",
        name="Fill missing",
        description="Replaces nulls and blanks in the listed columns with one value.",
        table_fn=columnar.fill_missing,
        parameters=_object(
            FieldSpec("columns", FieldType.ARRAY),
            FieldSpec("value", FieldType.ANY, required=False, default=0),
        ),
    ),
    Transform(
        key="drop_duplicates",
        name="Drop duplicates",
        description="Keeps the first row for each distinct key; whole row if no key given.",
        table_fn=columnar.drop_duplicates,
        parameters=_object(
            FieldSpec("columns", FieldType.ARRAY, required=False,
                      description="key columns (default: every column)"),
        ),
    ),
    Transform(
        key="sort_rows",
        name="Sort rows",
        description="Orders rows by one column; nulls always sort last.",
        table_fn=columnar.sort_rows,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("descending", FieldType.BOOLEAN, required=False, default=False),
        ),
    ),
    Transform(
        key="limit_rows",
        name="Limit rows",
        description="Keeps the first (or last) N rows.",
        table_fn=columnar.limit_rows,
        parameters=_object(
            FieldSpec("count", FieldType.INTEGER, required=False, default=100),
            FieldSpec("from_end", FieldType.BOOLEAN, required=False, default=False),
        ),
    ),
    Transform(
        key="datetime_parts",
        name="Datetime parts",
        description="Splits a timestamp into year / month / day / hour / week / quarter.",
        table_fn=columnar.datetime_parts,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("parts", FieldType.ARRAY, required=False,
                      description="year, month, day, hour, dayofyear, week, quarter, date"),
            FieldSpec("prefix", FieldType.STRING, required=False,
                      description="output prefix (default: the column name)"),
        ),
    ),
    Transform(
        key="duration_between",
        name="Duration between",
        description="Elapsed time between two timestamps, in hours, days or minutes.",
        table_fn=columnar.duration_between,
        parameters=_object(
            FieldSpec("start", FieldType.STRING),
            FieldSpec("end", FieldType.STRING),
            FieldSpec("unit", FieldType.STRING, required=False, default="hours",
                      enum=("hours", "days", "minutes")),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="bin_numeric",
        name="Bin numeric",
        description="Cuts a numeric column into named bands at the given edges.",
        table_fn=columnar.bin_numeric,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("edges", FieldType.ARRAY, description="ascending boundaries"),
            FieldSpec("labels", FieldType.ARRAY, required=False,
                      description="one label per band"),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="map_values",
        name="Map values",
        description="Translates coded values into readable ones via a lookup table.",
        table_fn=columnar.map_values,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("mapping", FieldType.JSON, description="code → label"),
            FieldSpec("default", FieldType.ANY, required=False),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="extract_pattern",
        name="Extract pattern",
        description="Pulls a regex match out of a text column into a new column.",
        table_fn=columnar.extract_pattern,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("pattern", FieldType.STRING, description="regular expression"),
            FieldSpec("group", FieldType.INTEGER, required=False, default=0),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="flag_rows",
        name="Flag rows",
        description="Marks rows matching a condition instead of removing them.",
        table_fn=columnar.flag_rows,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("op", FieldType.STRING, required=False, default="not_empty",
                      enum=("not_empty", "is_empty", "equals", "contains",
                            "gt", "gte", "lt", "lte")),
            FieldSpec("value", FieldType.ANY, required=False),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="summarise",
        name="Summarise",
        description="Groups by one or more columns, aggregating several measures at once.",
        table_fn=columnar.summarise,
        parameters=_object(
            FieldSpec("group_by", FieldType.ARRAY),
            FieldSpec("measures", FieldType.JSON,
                      description="column → sum | mean | min | max | count | median"),
        ),
    ),
    Transform(
        key="rank_rows",
        name="Rank rows",
        description="Adds a 1-based rank over a numeric column.",
        table_fn=columnar.rank_rows,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("descending", FieldType.BOOLEAN, required=False, default=True),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="percent_of_total",
        name="Percent of total",
        description="Expresses a column as a percentage share of its own total.",
        table_fn=columnar.percent_of_total,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
]

#  Reshaping and time-series. Kept as a second list rather than merged into the
#  one above because they answer a different question: everything above treats a
#  row as the unit of analysis, everything here treats a *series* as one.
_TIMESERIES = [
    Transform(
        key="pivot_wider",
        name="Pivot wider",
        description=(
            "Long readings in, one column per measurement out. The transform "
            "that makes a measurement store analysable."
        ),
        table_fn=timeseries.pivot_wider,
        parameters=_object(
            FieldSpec("keys", FieldType.ARRAY,
                      description="columns identifying a row, e.g. timestamp + asset",
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("name_from", FieldType.STRING,
                      description="column whose values become column names"),
            FieldSpec("value_from", FieldType.STRING,
                      description="column holding the value for each cell"),
            FieldSpec("aggregation", FieldType.STRING, required=False, default="mean",
                      enum=timeseries.PIVOT_AGGREGATIONS,
                      description="how repeated readings in one cell are combined"),
            FieldSpec("prefix", FieldType.STRING, required=False,
                      description="prepended to every generated column name"),
        ),
    ),
    Transform(
        key="unpivot_longer",
        name="Unpivot longer",
        description="Wide columns in, long readings out. The inverse of pivot wider.",
        table_fn=timeseries.unpivot_longer,
        parameters=_object(
            FieldSpec("keys", FieldType.ARRAY, required=False,
                      description="identifier columns to keep on every row",
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("columns", FieldType.ARRAY, required=False,
                      description="columns to melt (default: everything not a key)",
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("name_to", FieldType.STRING, required=False,
                      default="measurement"),
            FieldSpec("value_to", FieldType.STRING, required=False, default="value"),
            FieldSpec("drop_missing", FieldType.BOOLEAN, required=False, default=True),
        ),
    ),
    Transform(
        key="resample_time",
        name="Resample time",
        description=(
            "Collapses irregular readings onto fixed periods, so two series "
            "sampled at different moments can be compared at all."
        ),
        table_fn=timeseries.resample_time,
        parameters=_object(
            FieldSpec("timestamp", FieldType.STRING, description="the time column"),
            FieldSpec("period", FieldType.STRING, required=False, default="day",
                      enum=("hour", "day", "week", "month", "quarter", "year")),
            FieldSpec("measures", FieldType.JSON,
                      description="column → mean | sum | min | max | median | count "
                                  "| first | last | std; a list asks for several "
                                  "of one column, e.g. [\"mean\", \"max\", \"std\"]"),
            FieldSpec("group_by", FieldType.ARRAY, required=False,
                      description="columns that keep series apart, e.g. the subject id",
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("output", FieldType.STRING, required=False, default="period",
                      description="name of the generated period column"),
        ),
    ),
    Transform(
        key="rolling_stats",
        name="Rolling statistics",
        description=(
            "A trailing window per group, in time order: mean, spread, median, "
            "slope or z-score. Slope is how a rising value is seen before it "
            "reaches its limit."
        ),
        table_fn=timeseries.rolling_stats,
        parameters=_object(
            FieldSpec("column", FieldType.STRING, description="the numeric column"),
            FieldSpec("window", FieldType.INTEGER, required=False, default=7,
                      description="how many readings the window holds"),
            FieldSpec("statistics", FieldType.ARRAY, required=False,
                      description="mean, std, median, min, max, sum, count, slope, "
                                  "r_squared, zscore, range",
                      item=FieldSpec(name="statistic", type=FieldType.STRING,
                                     enum=timeseries.ROLLING_STATISTICS)),
            FieldSpec("group_by", FieldType.ARRAY, required=False,
                      description="the window restarts at each group",
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("order_by", FieldType.STRING, required=False,
                      description="ordering column, normally the timestamp"),
            FieldSpec("min_periods", FieldType.INTEGER, required=False, default=2,
                      description="readings needed before an answer is given"),
            FieldSpec("per", FieldType.STRING, required=False, default="hour",
                      enum=("second", "minute", "hour", "day"),
                      description="the unit a rolling slope is reported in"),
            FieldSpec("prefix", FieldType.STRING, required=False,
                      description="output prefix; defaults to <column>_roll"),
        ),
    ),
    Transform(
        key="rate_of_change",
        name="Rate of change",
        description=(
            "Delta value over delta time, per hour or per day — comparable "
            "between series that were not sampled at the same interval."
        ),
        table_fn=timeseries.rate_of_change,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("periods", FieldType.INTEGER, required=False, default=1,
                      description="how many readings back to compare against"),
            FieldSpec("per", FieldType.STRING, required=False, default="hour",
                      enum=("second", "minute", "hour", "day")),
            FieldSpec("group_by", FieldType.ARRAY, required=False,
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("order_by", FieldType.STRING, required=False),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="lag_column",
        name="Lag column",
        description="What this series read N readings ago, per group and in time order.",
        table_fn=timeseries.lag_column,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("periods", FieldType.INTEGER, required=False, default=1,
                      description="positive looks back, negative looks forward"),
            FieldSpec("group_by", FieldType.ARRAY, required=False,
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("order_by", FieldType.STRING, required=False),
            FieldSpec("output", FieldType.STRING, required=False),
        ),
    ),
    Transform(
        key="linear_trend",
        name="Linear trend",
        description=(
            "One row per group: slope, R², first-to-last change and a stated "
            "direction — with 'unstable' when the line is fitted through noise."
        ),
        table_fn=timeseries.linear_trend,
        parameters=_object(
            FieldSpec("column", FieldType.STRING),
            FieldSpec("group_by", FieldType.ARRAY, required=False,
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("order_by", FieldType.STRING, required=False,
                      description="the time column the slope is measured against"),
            FieldSpec("per", FieldType.STRING, required=False, default="day",
                      enum=("second", "minute", "hour", "day")),
            FieldSpec("min_periods", FieldType.INTEGER, required=False, default=3),
            FieldSpec("prefix", FieldType.STRING, required=False,
                      description="output prefix; defaults to the column name"),
        ),
    ),
    Transform(
        key="correlation",
        name="Correlation",
        description=(
            "Pairwise Pearson correlation between measurements, per group, with "
            "the number of points it was computed from beside it."
        ),
        table_fn=timeseries.correlation,
        parameters=_object(
            FieldSpec("columns", FieldType.ARRAY,
                      description="two or more numeric columns to pair up",
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("group_by", FieldType.ARRAY, required=False,
                      item=FieldSpec(name="column", type=FieldType.STRING)),
            FieldSpec("min_periods", FieldType.INTEGER, required=False, default=5,
                      description="below this the coefficient is left null"),
        ),
    ),
]


def register_standard_transforms() -> None:
    for transform in (*_STANDARD, *_TIMESERIES):
        register(transform)
