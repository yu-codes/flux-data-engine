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

from . import columnar
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


def register_standard_transforms() -> None:
    for transform in _STANDARD:
        register(transform)
