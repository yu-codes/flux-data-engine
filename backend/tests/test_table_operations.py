"""Filtering, sorting and grouping now happen in Arrow, not in Python loops.

The risk in that move is silent behaviour change: a filter that used to match
because both sides were stringified, a sort that used to put nulls somewhere
else. So the old row-based implementations are kept here as an oracle and the
new ones are required to agree with them on a deliberately untidy table -
mixed types, nulls, numbers stored as text, values that only match once they
are printed.

If a future change to `table_ops` disagrees with the oracle, that is either a
bug or a decision that has to be made explicitly by changing the oracle too.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.shared.errors import ValidationError
from app.shared.tabular import Table


# --------------------------------------------------------------------------
# the oracle: how the platform behaved before, in plain Python
# --------------------------------------------------------------------------
def _same(left: Any, right: Any) -> bool:
    if left == right:
        return True
    if left is None or right is None:
        return False
    return str(left) == str(right)


_OPERATORS = {
    "eq": _same,
    "ne": lambda a, b: not _same(a, b),
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "contains": lambda a, b: b is not None and str(b).lower() in str(a or "").lower(),
    "in": lambda a, b: any(_same(a, option) for option in (b or [])),
    "is_null": lambda a, _: a is None,
    "not_null": lambda a, _: a is not None,
}


def _safe(operator, left, right) -> bool:
    try:
        return bool(operator(left, right))
    except TypeError:
        return False


def rows_filtered(rows: list[dict], conditions: list[dict]) -> list[dict]:
    for spec in conditions:
        operator = _OPERATORS[spec.get("op", "eq")]
        column, target = spec["column"], spec.get("value")
        rows = [r for r in rows if _safe(operator, r.get(column), target)]
    return rows


def rows_sorted(rows: list[dict], column: str, descending: bool) -> list[dict]:
    def key(value):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        return str(value or "")

    return sorted(
        rows,
        key=lambda r: (r.get(column) is None, key(r.get(column))),
        reverse=descending,
    )


# --------------------------------------------------------------------------
# a table with the awkward cases in it
# --------------------------------------------------------------------------
SALES = [
    {"city": "Taipei", "units": 10, "price": 25.5, "active": True, "note": "urgent"},
    {"city": "Kaohsiung", "units": 3, "price": 60.0, "active": False, "note": "URGENT"},
    {"city": "Taichung", "units": 25, "price": 12.25, "active": True, "note": "later"},
    {"city": "Tainan", "units": None, "price": 99.0, "active": False, "note": None},
    {"city": "Taipei", "units": 7, "price": None, "active": True, "note": "urgent-ish"},
]


@pytest.fixture(scope="module")
def table() -> Table:
    return Table.from_rows(SALES)


CASES = [
    [{"column": "city", "op": "eq", "value": "Taipei"}],
    [{"column": "city", "op": "ne", "value": "Taipei"}],
    #  A number typed into a text box arrives as a string.
    [{"column": "units", "op": "eq", "value": "10"}],
    [{"column": "units", "op": "gte", "value": 7}],
    [{"column": "units", "op": "lt", "value": 10}],
    [{"column": "units", "op": "lte", "value": 10}],
    [{"column": "note", "op": "contains", "value": "urgent"}],
    [{"column": "note", "op": "contains", "value": "URGENT"}],
    [{"column": "city", "op": "in", "value": ["Taipei", "Tainan"]}],
    [{"column": "units", "op": "in", "value": ["3", "25"]}],
    [{"column": "units", "op": "is_null", "value": None}],
    [{"column": "note", "op": "not_null", "value": None}],
    #  Two conditions compose.
    [
        {"column": "city", "op": "eq", "value": "Taipei"},
        {"column": "units", "op": "gt", "value": 8},
    ],
]


@pytest.mark.parametrize("conditions", CASES, ids=[str(c) for c in CASES])
def test_arrow_filtering_agrees_with_the_row_implementation(table, conditions):
    expected = rows_filtered([dict(r) for r in SALES], conditions)
    actual = table.filter(conditions).to_rows()
    assert actual == expected


@pytest.mark.parametrize("column", ["city", "units", "price", "note"])
def test_ascending_sort_agrees_with_the_row_implementation(table, column):
    expected = rows_sorted([dict(r) for r in SALES], column, False)
    actual = table.sort(column, False).to_rows()
    assert [r[column] for r in actual] == [r[column] for r in expected]


# --------------------------------------------------------------------------
# where the new implementation deliberately differs
#
# Three cases where the row-based code was wrong rather than merely different.
# Each is pinned here with the old behaviour named, so that "Arrow does not
# match the oracle" is a decision on the record instead of a regression nobody
# noticed.
# --------------------------------------------------------------------------
def test_descending_sort_keeps_nulls_at_the_bottom(table):
    """Old behaviour: reversing the key floated every null to the top.

    Sorting a column descending is how somebody asks for the largest values.
    The row version reversed its whole sort key, nulls included, so the first
    screen of "highest first" was filled with rows that have no value at all.
    """
    units = [row["units"] for row in table.sort("units", True).to_rows()]
    assert units == [25, 10, 7, 3, None]

    notes = [row["note"] for row in table.sort("note", True).to_rows()]
    assert notes[-1] is None


def test_a_boolean_column_can_be_filtered_from_a_text_box(table):
    """Old behaviour: matched nothing.

    The Explore page sends the string "true", and the row version compared it
    with `str(True) == "true"` - "True" != "true", so a boolean filter in the
    UI silently returned an empty table. Coercing the value to the column's
    type is what the user meant.
    """
    matched = table.filter([{"column": "active", "op": "eq", "value": "true"}]).to_rows()
    assert [row["city"] for row in matched] == ["Taipei", "Taichung", "Taipei"]

    #  And the oracle really did return nothing, which is why this is a fix.
    assert rows_filtered([dict(r) for r in SALES],
                         [{"column": "active", "op": "eq", "value": "true"}]) == []


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [("units", "5", [10, 25, 7]), ("price", "20", [25.5, 60.0, 99.0])],
)
def test_ordered_comparisons_accept_a_numeric_string(table, column, value, expected):
    """Old behaviour: raised TypeError internally and matched nothing.

    `10 > "5"` is a TypeError in Python, which the row version swallowed and
    treated as "does not match". An API client sending its filter values as
    strings - which is what a form does - got an empty result with no error.
    """
    matched = table.filter([{"column": column, "op": "gt", "value": value}]).to_rows()
    assert [row[column] for row in matched] == expected

    assert rows_filtered([dict(r) for r in SALES],
                         [{"column": column, "op": "gt", "value": value}]) == []


def test_a_filter_on_a_column_that_is_not_there_is_an_error(table):
    with pytest.raises(ValidationError):
        table.filter([{"column": "nope", "op": "eq", "value": 1}])


def test_an_unknown_operator_is_an_error(table):
    with pytest.raises(ValidationError):
        table.filter([{"column": "city", "op": "regex", "value": "T.*"}])


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def test_grouping_sums_per_group(table):
    grouped = {row["city"]: row["units_sum"] for row in
               table.group_aggregate("city", ["units"], "sum").to_rows()}
    assert grouped["Taipei"] == 17.0        # 10 + 7
    assert grouped["Taichung"] == 25.0
    #  A group whose only value is null aggregates to null, not to zero:
    #  "we have no figure" and "the figure is zero" are different answers.
    assert grouped["Tainan"] is None


def test_grouping_means_ignore_missing_values(table):
    grouped = {row["city"]: row["price_mean"] for row in
               table.group_aggregate("city", ["price"], "mean").to_rows()}
    #  Taipei has 25.5 and a null; the mean is of what is there.
    assert grouped["Taipei"] == 25.5


def test_numbers_stored_as_text_still_aggregate():
    """Real files store numbers as strings, and the row version coped."""
    table = Table.from_rows(
        [{"g": "a", "v": "1.5"}, {"g": "a", "v": "2.5"}, {"g": "b", "v": "oops"}]
    )
    summed = table.group_aggregate("g", ["v"], "sum").to_rows()
    grouped = {r["g"]: r["v_sum"] for r in summed}
    assert grouped["a"] == 4.0
    assert grouped["b"] is None


def test_aggregating_a_column_that_is_not_there_is_an_error(table):
    with pytest.raises(ValidationError):
        table.group_aggregate("city", ["nope"], "sum")


# --------------------------------------------------------------------------
# reading values back
# --------------------------------------------------------------------------
def test_numeric_values_drops_nulls_and_non_numbers(table):
    assert sorted(table.numeric_values("units")) == [3.0, 7.0, 10.0, 25.0]


def test_stats_are_computed_without_materialising_rows(table):
    assert table.stats("units") == {"min": 3.0, "max": 25.0, "mean": 11.25}
    #  A column with nothing numeric in it says so by being empty.
    assert table.stats("city") == {}


def test_distinct_preserves_the_values_not_their_order(table):
    assert set(table.distinct("city")) == {"Taipei", "Kaohsiung", "Taichung", "Tainan"}


def test_column_values_avoids_building_a_dict_per_row(table):
    assert table.column_values("city")[:2] == ["Taipei", "Kaohsiung"]


def test_a_column_that_is_not_there_reads_as_nulls(table):
    """One rule for a missing column, so callers do not each invent their own.

    `row.get(name)` answered None, and code written against rows relied on it:
    a chart split by a column the dataset lacks drew one unnamed band. Answering
    with an empty list instead turned that into a length mismatch several
    layers away from the cause.
    """
    absent = table.column_values("absent")
    assert absent == [None] * table.num_rows


# --------------------------------------------------------------------------
# parquet pushdown
# --------------------------------------------------------------------------
def test_reading_only_the_columns_asked_for(tmp_path):
    """Projection pushdown: a wide file, four columns read."""
    wide = Table.from_rows(
        [{f"c{i}": i for i in range(40)} | {"keep": n} for n in range(50)]
    )
    path = wide.write_parquet(tmp_path / "wide.parquet")

    narrow = Table.from_parquet(path, columns=["keep", "c0"])
    assert narrow.columns == ["keep", "c0"]
    assert narrow.num_rows == 50
    #  And the row count is available without reading the file at all.
    assert Table.parquet_row_count(path) == 50
