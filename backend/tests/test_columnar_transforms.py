"""Twenty-two transforms moved from rows into Arrow. Same answers, no dicts.

Rewriting a transform is where a pipeline quietly changes its output: a sort
that used to be stable, a deduplication that used to keep the first row rather
than an arbitrary one, a rename that used to leave untouched columns alone. So
the row-based originals are kept in `row_oracles.py` - verbatim, the same code
that was deleted from the library - and the Arrow versions are required to
agree with them on a table containing the awkward cases.

The table below is untidy on purpose. Numbers stored as text with a thousands
separator, a null in a measure, an exact duplicate row, a timestamp in two
formats, a column that most steps never mention: every one of those is a way a
naive port produces a different answer while still looking correct.

The second thing these tests check is that the rewrite was worth doing at all:
no transform may materialise its input as rows, and that is asserted directly
rather than assumed.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.plugins.python_function import library
from app.shared.errors import ValidationError
from app.shared.tabular import Table
from tests import row_oracles as rows

# --------------------------------------------------------------------------
STORMS: list[dict[str, Any]] = [
    {
        "name": "Nari",
        "wind": 40,
        "pressure_text": "962 hPa",
        "year": 2001,
        "landfall": "north",
        "formed": "2001-09-06 08:00:00",
        "ended": "2001-09-20 14:00:00",
        "rain": "1,944",
        "note": "long lived",
    },
    {
        "name": "Morakot",
        "wind": 40,
        "pressure_text": "945 hPa",
        "year": 2009,
        "landfall": "south",
        "formed": "2009/08/04",
        "ended": "2009-08-10 06:00:00",
        "rain": "2,884",
        "note": "record rainfall",
    },
    {
        "name": "Soudelor",
        "wind": 48,
        "pressure_text": "no data",
        "year": 2015,
        "landfall": "north",
        "formed": "2015-08-06",
        "ended": "2015-08-09",
        "rain": "820",
        "note": "",
    },
    {
        #  An exact repeat of the first row.
        "name": "Nari",
        "wind": 40,
        "pressure_text": "962 hPa",
        "year": 2001,
        "landfall": "north",
        "formed": "2001-09-06 08:00:00",
        "ended": "2001-09-20 14:00:00",
        "rain": "1,944",
        "note": "long lived",
    },
    {
        "name": "Haikui",
        "wind": None,
        "pressure_text": None,
        "year": 2023,
        "landfall": "south",
        "formed": "not a date",
        "ended": None,
        "rain": None,
        "note": None,
    },
]


@pytest.fixture
def table() -> Table:
    return Table.from_rows(STORMS)


def apply(key: str, table: Table, **options) -> list[dict]:
    return library.get(key).apply(table, options).to_rows()


def oracle(fn, **options) -> list[dict]:
    """What the row implementation answered, on the same input.

    Through Arrow on the way out, because that is what the old adapter did:
    `Table.from_rows(fn(table.to_rows(), options))`. Comparing against the raw
    dicts would hold the rewrite to a behaviour the pipeline never had - a
    blank filled with the integer 0 came back as "0" once the column it landed
    in was text.
    """
    return Table.from_rows(fn([dict(row) for row in STORMS], options)).to_rows()


def agree(key: str, table: Table, fn, **options) -> None:
    assert apply(key, table, **options) == oracle(fn, **options)


# --------------------------------------------------------------------------
# equivalence: shape
# --------------------------------------------------------------------------
def test_rename_columns_matches_the_row_version(table):
    renamed = apply("rename_columns", table, mapping={"wind": "wind_ms", "year": "season"})
    assert list(renamed[0]) == [
        "name", "wind_ms", "pressure_text", "season",
        "landfall", "formed", "ended", "rain", "note",
    ]


def test_drop_and_select_keep_the_stated_columns(table):
    assert set(apply("drop_columns", table, columns=["note", "rain"])[0]) == {
        "name", "wind", "pressure_text", "year", "landfall", "formed", "ended",
    }
    assert list(apply("select_columns", table, columns=["name", "year"])[0]) == [
        "name", "year",
    ]


@pytest.mark.parametrize("column", ["name", "wind", "year"])
def test_ascending_sort_matches_the_row_version(table, column):
    expected = sorted(
        [dict(r) for r in STORMS],
        key=lambda row: (row.get(column) is None, rows._as_number(row.get(column))
                         if rows._as_number(row.get(column)) is not None
                         else (1, str(row.get(column)))),
    )
    actual = [row[column] for row in apply("sort_rows", table, column=column)]
    assert actual == [row[column] for row in expected]


def test_descending_sort_keeps_nulls_last(table):
    """The row version floated nulls to the top when reversed; this does not.

    Same deliberate change as the Explore sort: "highest first" should not
    open with the rows that have no value.
    """
    sorted_rows = apply("sort_rows", table, column="wind", descending=True)
    winds = [row["wind"] for row in sorted_rows]
    assert winds == [48, 40, 40, 40, None]


@pytest.mark.parametrize("from_end", [False, True])
def test_limit_matches_the_row_version(table, from_end):
    limited = apply("limit_rows", table, count=2, from_end=from_end)
    assert limited == (STORMS[-2:] if from_end else STORMS[:2])


def test_drop_duplicates_keeps_the_first_row_and_the_original_order(table):
    kept = apply("drop_duplicates", table, columns=["name"])
    assert [row["name"] for row in kept] == ["Nari", "Morakot", "Soudelor", "Haikui"]
    assert len(apply("drop_duplicates", table)) == 4


def test_deduplicating_on_a_column_with_nulls_treats_them_as_one_value():
    table = Table.from_rows([{"a": None}, {"a": None}, {"a": 1}])
    assert apply("drop_duplicates", table, columns=["a"]) == [{"a": None}, {"a": 1}]


# --------------------------------------------------------------------------
# equivalence: rows kept
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "options",
    [
        {"column": "wind", "op": "not_empty"},
        {"column": "wind", "op": "gt", "value": 39},
        {"column": "wind", "op": "gte", "value": 40},
        {"column": "wind", "op": "lt", "value": 48},
        {"column": "wind", "op": "lte", "value": 40},
        {"column": "landfall", "op": "equals", "value": "north"},
        {"column": "landfall", "op": "not_equals", "value": "north"},
        {"column": "name", "op": "in", "value": ["Nari", "Haikui"]},
        {"column": "note", "op": "is_empty"},
    ],
)
def test_filter_rows_matches_the_row_version(table, options):
    agree("filter_rows", table, rows._filter_rows, **options)


def test_ordering_a_numeric_text_column_still_matches_nothing(table):
    """`rain` holds "1,944". Both versions refuse to order it as a number.

    Worth pinning rather than fixing here: the step that makes such a column
    comparable is `parse_numeric`, and a filter that quietly started parsing
    would change what pipelines already in use select.
    """
    with pytest.raises(ValidationError, match="removed every row"):
        apply("filter_rows", table, column="rain", op="gt", value=1000)
    with pytest.raises(ValidationError, match="removed every row"):
        rows._filter_rows([dict(r) for r in STORMS],
                          {"column": "rain", "op": "gt", "value": 1000})


def test_a_filter_that_removes_everything_is_refused(table):
    with pytest.raises(ValidationError, match="removed every row"):
        apply("filter_rows", table, column="wind", op="gt", value=999)


# --------------------------------------------------------------------------
# equivalence: derived columns
# --------------------------------------------------------------------------
def test_cast_types_matches_the_row_version(table):
    agree(
        "cast_types", table, rows._cast_types,
        casts={"rain": "number", "wind": "integer", "year": "text", "note": "boolean"},
    )


def test_casting_a_column_in_place_does_not_move_it(table):
    """A step that reorders columns changes what every step after it reads."""
    cast = apply("cast_types", table, casts={"wind": "text"})
    assert list(cast[0]) == list(STORMS[0])


def test_fill_missing_matches_the_row_version(table):
    agree("fill_missing", table, rows._fill_missing, columns=["wind", "note"], value=0)


def test_parse_numeric_matches_the_row_version(table):
    agree("parse_numeric", table, rows._parse_numeric, column="pressure_text")
    agree(
        "parse_numeric", table, rows._parse_numeric,
        column="pressure_text", keep_original=False, output="pressure",
    )


def test_datetime_parts_matches_the_row_version(table):
    agree(
        "datetime_parts", table, rows._datetime_parts,
        column="formed", parts=["year", "month", "quarter", "week", "dayofyear", "date"],
    )


def test_duration_between_matches_the_row_version(table):
    for unit in ("hours", "days", "minutes"):
        agree("duration_between", table, rows._duration_between,
              start="formed", end="ended", unit=unit)


def test_bin_numeric_matches_the_row_version(table):
    agree(
        "bin_numeric", table, rows._bin_numeric,
        column="wind", edges=[17.2, 32.7, 51.0], labels=["mild", "moderate"],
    )


def test_binning_puts_a_value_on_an_edge_in_exactly_one_band(table):
    banded = apply("bin_numeric", table, column="wind", edges=[40, 48], labels=["only"])
    assert [row["wind_band"] for row in banded] == [
        "only", "only", "only", "only", None,
    ]


def test_map_values_matches_the_row_version(table):
    agree("map_values", table, rows._map_values,
          column="landfall", mapping={"north": "北部", "south": "南部"})
    agree("map_values", table, rows._map_values,
          column="year", mapping={"2001": "early"}, default="later")


def test_extract_pattern_matches_the_row_version(table):
    agree("extract_pattern", table, rows._extract_pattern,
          column="pressure_text", pattern=r"(\d+)", group=1)


def test_flag_rows_matches_the_row_version(table):
    for options in (
        {"column": "wind", "op": "gt", "value": 45},
        {"column": "note", "op": "contains", "value": "rain"},
        {"column": "note", "op": "is_empty"},
        {"column": "rain", "op": "gte", "value": 1944},
    ):
        agree("flag_rows", table, rows._flag_rows, **options)


def test_moving_average_matches_the_row_version(table):
    agree("moving_average", table, rows._moving_average, column="wind", window=2)


def test_zscore_outliers_matches_the_row_version(table):
    agree("zscore_outliers", table, rows._zscore_outliers, column="wind", threshold=1.0)


def test_rank_rows_matches_the_row_version(table):
    agree("rank_rows", table, rows._rank_rows, column="wind")
    agree("rank_rows", table, rows._rank_rows, column="wind", descending=False)


def test_ranking_is_stable_across_ties(table):
    """Equal values keep the order they arrived in, or a rerun renumbers them."""
    ranked = apply("rank_rows", table, column="wind", descending=True)
    assert [row["wind_rank"] for row in ranked] == [2, 3, 1, 4, None]


def test_percent_of_total_matches_the_row_version(table):
    agree("percent_of_total", table, rows._percent_of_total, column="wind")


# --------------------------------------------------------------------------
# equivalence: aggregation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("how", ["sum", "mean", "min", "max", "count"])
def test_group_aggregate_matches_the_row_version(table, how):
    agree("group_aggregate", table, rows._group_aggregate,
          group_by="landfall", column="wind", agg=how)


def test_group_aggregate_still_ignores_numeric_text(table):
    """`rain` is text. It aggregated to nothing before, and must still.

    Not a wart worth fixing here: a pipeline that put `cast_types` in front of
    this step would start double-counting if the rule quietly changed.
    """
    assert apply("group_aggregate", table, group_by="landfall", column="rain") == []


def test_summarise_matches_the_row_version(table):
    agree(
        "summarise", table, rows._summarise,
        group_by=["landfall"], measures={"wind": "mean", "rain": "median", "year": "count"},
    )


def test_summarise_over_several_keys_matches_the_row_version(table):
    agree(
        "summarise", table, rows._summarise,
        group_by=["landfall", "year"], measures={"wind": "max"},
    )


def test_summarise_reads_numeric_text_where_group_aggregate_does_not(table):
    """The two have always differed, and something in the field relies on it."""
    summary = apply("summarise", table, group_by=["landfall"], measures={"rain": "sum"})
    #  north is 1,944 + 820 + the duplicate 1,944.
    assert [row["rain_sum"] for row in summary] == [4708.0, 2884.0]


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("key", "options"),
    [
        ("select_columns", {"columns": ["nope"]}),
        ("select_columns", {"columns": []}),
        ("drop_columns", {"columns": []}),
        ("rename_columns", {"mapping": {}}),
        ("sort_rows", {"column": "nope"}),
        ("limit_rows", {"count": 0}),
        ("drop_duplicates", {"columns": ["nope"]}),
        ("cast_types", {"casts": {}}),
        ("cast_types", {"casts": {"wind": "colour"}}),
        ("fill_missing", {"columns": []}),
        ("filter_rows", {"column": "wind", "op": "sideways"}),
        ("flag_rows", {"column": "wind", "op": "sideways"}),
        ("bin_numeric", {"column": "wind", "edges": [1]}),
        ("bin_numeric", {"column": "wind", "edges": [5, 1]}),
        ("bin_numeric", {"column": "wind", "edges": [1, 2, 3], "labels": ["one"]}),
        ("map_values", {"column": "landfall", "mapping": {}}),
        ("extract_pattern", {"column": "note", "pattern": ""}),
        ("extract_pattern", {"column": "note", "pattern": "("}),
        ("extract_pattern", {"column": "note", "pattern": "a" * 201}),
        ("datetime_parts", {"column": "formed", "parts": ["fortnight"]}),
        ("datetime_parts", {"column": "nope"}),
        ("duration_between", {"start": "formed", "end": "ended", "unit": "fortnights"}),
        ("moving_average", {"column": "wind", "window": 0}),
        ("zscore_outliers", {"column": "note"}),
        ("rank_rows", {"column": "nope"}),
        ("percent_of_total", {"column": "note"}),
        ("summarise", {"group_by": ["landfall"], "measures": {"wind": "median-ish"}}),
        ("group_aggregate", {"group_by": "landfall", "column": "wind", "agg": "product"}),
    ],
)
def test_bad_options_are_refused_with_a_reason(table, key, options):
    with pytest.raises(ValidationError):
        apply(key, table, **options)


def test_a_bad_operator_is_refused_even_when_there_are_no_rows():
    """The row version only noticed once it reached a row to test.

    A deliberate change: an operator that does not exist is a broken step
    whether or not any data arrived, and a pipeline should say so at the step
    that is wrong rather than at the next one that finds an empty table.
    """
    empty = Table.from_rows([])
    with pytest.raises(ValidationError):
        apply("flag_rows", empty, column="wind", op="sideways")


# --------------------------------------------------------------------------
# and that it was worth doing
# --------------------------------------------------------------------------
#  Options that exercise each transform on the fixture table above.
EXERCISE: dict[str, dict[str, Any]] = {
    "select_columns": {"columns": ["name"]},
    "drop_columns": {"columns": ["wind"]},
    "rename_columns": {"mapping": {"wind": "wind_ms"}},
    "sort_rows": {"column": "year"},
    "limit_rows": {"count": 2},
    "drop_duplicates": {"columns": ["name"]},
    "filter_rows": {"column": "wind", "op": "not_empty"},
    "cast_types": {"casts": {"rain": "number"}},
    "fill_missing": {"columns": ["wind"], "value": 0},
    "parse_numeric": {"column": "pressure_text"},
    "datetime_parts": {"column": "formed", "parts": ["year"]},
    "duration_between": {"start": "formed", "end": "ended"},
    "bin_numeric": {"column": "wind", "edges": [0, 45, 100]},
    "map_values": {"column": "landfall", "mapping": {"north": "北部"}},
    "extract_pattern": {"column": "pressure_text", "pattern": r"\d+"},
    "flag_rows": {"column": "wind", "op": "gt", "value": 45},
    "moving_average": {"column": "wind", "window": 2},
    "zscore_outliers": {"column": "wind"},
    "rank_rows": {"column": "wind"},
    "percent_of_total": {"column": "wind"},
    "group_aggregate": {"group_by": "landfall", "column": "wind"},
    "summarise": {"group_by": ["landfall"], "measures": {"wind": "mean"}},
}


def test_every_transform_is_exercised_here():
    """A transform added without a case in this file is a transform untested."""
    assert set(EXERCISE) == set(library.keys())


@pytest.mark.parametrize("key", sorted(EXERCISE))
def test_every_transform_declares_itself_columnar(key):
    assert library.get(key).is_columnar is True


@pytest.mark.parametrize("key", sorted(EXERCISE))
def test_no_transform_materialises_its_input_as_rows(monkeypatch, table, key):
    """The whole point of the rewrite, asserted rather than assumed.

    Aggregating transforms build their *output* from rows - a handful of them -
    and that is fine. What must never happen is the input being turned into
    dicts, which is what made a twelve-step pipeline cost twelve full copies.
    """
    built: list[int] = []
    original = Table.to_rows

    def counting(self, limit=None, offset=0):
        result = original(self, limit=limit, offset=offset)
        built.append(len(result))
        return result

    monkeypatch.setattr(Table, "to_rows", counting)
    library.get(key).apply(table, EXERCISE[key])
    assert built == [], f"{key} materialised {built} rows"


def test_a_wide_table_only_costs_the_columns_a_step_names(monkeypatch):
    """Reading one column out of forty should not read forty.

    This is the difference the rewrite was for, stated as a number rather than
    as a claim: the typhoon catalogue is forty columns wide and most steps
    touch one of them.
    """
    read: list[str] = []
    original = Table.column_values

    def counting(self, column):
        read.append(column)
        return original(self, column)

    monkeypatch.setattr(Table, "column_values", counting)
    wide = Table.from_rows([{f"c{i}": i for i in range(40)} for _ in range(50)])
    library.get("flag_rows").apply(wide, {"column": "c7", "op": "gt", "value": 3})
    assert read == ["c7"]
