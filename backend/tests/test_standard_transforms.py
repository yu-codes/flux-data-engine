"""The standard transform vocabulary a pipeline is composed from.

Each transform takes a table and returns a table. That contract is what lets
steps chain freely, so it is what these tests hold to: the shape in, the shape
out, and the one thing each transform is for.
"""

from __future__ import annotations

import pytest

from app.plugins.python_function import library
from app.shared.errors import ValidationError
from app.shared.tabular import Table

RAW = [
    {
        "name": "Alpha",
        "wind": "30 (m/s)",
        "pressure": "960",
        "start": "2020-07-01 08:00:00",
        "end": "2020-07-04 20:00:00",
        "landfall": "宜蘭",
        "code": "1",
    },
    {
        "name": "Bravo",
        "wind": "55 (m/s)",
        "pressure": "",
        "start": "2021-08-11 00:00:00",
        "end": "2021-08-12 12:00:00",
        "landfall": "",
        "code": "3",
    },
    {
        "name": "Charlie",
        "wind": "18 (m/s)",
        "pressure": "998",
        "start": "2022-09-20 06:00:00",
        "end": "2022-09-22 06:00:00",
        "landfall": "花蓮",
        "code": "9",
    },
]


def run(key: str, rows: list[dict], **options) -> list[dict]:
    """Go through `apply`, which is what a pipeline actually calls.

    A transform may be written against rows or against a Table; `apply` is the
    seam that hides which, so exercising `fn` directly would test half of them
    and skip the half that were moved into Arrow.
    """
    return library.get(key).apply(Table.from_rows(rows), options).to_rows()


def test_the_catalogue_names_every_parameter():
    catalogue = {entry["key"]: entry for entry in library.catalogue()}
    assert {"cast_types", "bin_numeric", "summarise", "datetime_parts"} <= set(catalogue)
    for entry in catalogue.values():
        assert entry["name"] and entry["description"]
        assert entry["parameters"]["shape"] == "object"
        for field in entry["parameters"]["fields"]:
            assert field["name"] and field["type"]


def test_cast_types_turns_text_into_numbers():
    rows = run("cast_types", RAW, casts={"pressure": "number"})
    assert [row["pressure"] for row in rows] == [960.0, None, 998.0]


def test_cast_types_rejects_a_type_it_cannot_produce():
    with pytest.raises(ValidationError):
        run("cast_types", RAW, casts={"pressure": "decimal"})


def test_datetime_parts_makes_seasonality_askable():
    rows = run(
        "datetime_parts", RAW, column="start", prefix="genesis", parts=["month", "year"]
    )
    assert [row["genesis_month"] for row in rows] == [7, 8, 9]
    assert [row["genesis_year"] for row in rows] == [2020, 2021, 2022]


def test_datetime_parts_leaves_unparseable_timestamps_null():
    rows = run("datetime_parts", [{"t": "not a date"}], column="t", parts=["month"])
    assert rows[0]["t_month"] is None


def test_duration_between_measures_the_gap():
    rows = run("duration_between", RAW, start="start", end="end", unit="hours")
    assert [row["duration_hours"] for row in rows] == [84.0, 36.0, 48.0]


def test_duration_is_null_when_the_end_precedes_the_start():
    rows = run(
        "duration_between",
        [{"a": "2020-01-02 00:00:00", "b": "2020-01-01 00:00:00"}],
        start="a",
        end="b",
    )
    assert rows[0]["duration_hours"] is None


def test_bin_numeric_names_the_bands():
    parsed = run("parse_numeric", RAW, column="wind", output="wind_ms")
    rows = run(
        "bin_numeric",
        parsed,
        column="wind_ms",
        edges=[0, 17.2, 32.7, 51.0, 120.0],
        labels=["depression", "mild", "moderate", "severe"],
        output="band",
    )
    #  30 and 18 m/s are both mild; 55 is severe.
    assert [row["band"] for row in rows] == ["mild", "severe", "mild"]


def test_bin_numeric_closes_the_final_band_at_the_top_edge():
    rows = run(
        "bin_numeric", [{"v": 10}], column="v", edges=[0, 5, 10], labels=["low", "high"]
    )
    assert rows[0]["v_band"] == "high"


def test_bin_numeric_refuses_a_mismatched_label_count():
    with pytest.raises(ValidationError):
        run("bin_numeric", [{"v": 1}], column="v", edges=[0, 5, 10], labels=["only one"])


def test_bin_numeric_refuses_unordered_edges():
    with pytest.raises(ValidationError):
        run("bin_numeric", [{"v": 1}], column="v", edges=[10, 0])


def test_map_values_translates_codes_to_labels():
    rows = run("map_values", RAW, column="code", mapping={"1": "north", "3": "centre"},
               output="label", default="other")
    assert [row["label"] for row in rows] == ["north", "centre", "other"]


def test_flag_rows_keeps_the_denominator():
    rows = run("flag_rows", RAW, column="landfall", op="not_empty", output="made_landfall")
    assert len(rows) == len(RAW)
    assert [row["made_landfall"] for row in rows] == [True, False, True]


def test_summarise_aggregates_several_measures_at_once():
    parsed = run("parse_numeric", RAW, column="wind", output="wind_ms")
    banded = run("cast_types", parsed, casts={"pressure": "number"})
    rows = run(
        "summarise",
        banded,
        group_by=["code"],
        measures={"wind_ms": "mean", "pressure": "max"},
    )
    by_code = {row["code"]: row for row in rows}
    assert by_code["1"]["wind_ms_mean"] == 30.0
    assert by_code["1"]["pressure_max"] == 960.0
    assert by_code["3"]["pressure_max"] is None
    assert all(row["row_count"] == 1 for row in rows)


def test_summarise_rejects_an_aggregation_it_does_not_have():
    with pytest.raises(ValidationError):
        run("summarise", RAW, group_by=["code"], measures={"wind": "stddev"})


def test_rename_and_drop_reshape_without_touching_values():
    renamed = run("rename_columns", RAW, mapping={"name": "typhoon"})
    assert "typhoon" in renamed[0] and "name" not in renamed[0]
    trimmed = run("drop_columns", renamed, columns=["landfall", "code"])
    assert "landfall" not in trimmed[0]
    assert trimmed[0]["typhoon"] == "Alpha"


def test_sort_rows_puts_nulls_last():
    rows = run("sort_rows", [{"v": 3}, {"v": None}, {"v": 1}], column="v")
    assert [row["v"] for row in rows] == [1, 3, None]


def test_percent_of_total_sums_to_one_hundred():
    rows = run("percent_of_total", [{"v": 1}, {"v": 3}], column="v")
    assert [row["v_pct"] for row in rows] == [25.0, 75.0]


def test_percent_of_total_refuses_a_zero_total():
    with pytest.raises(ValidationError):
        run("percent_of_total", [{"v": 0}, {"v": 0}], column="v")


def test_rank_rows_ranks_highest_first():
    rows = run("rank_rows", [{"v": 5}, {"v": 9}, {"v": 1}], column="v")
    assert [row["v_rank"] for row in rows] == [2, 1, 3]


def test_extract_pattern_will_not_take_an_unbounded_pattern():
    with pytest.raises(ValidationError):
        run("extract_pattern", RAW, column="name", pattern="x" * 201)


def test_extract_pattern_rejects_an_invalid_regex():
    with pytest.raises(ValidationError):
        run("extract_pattern", RAW, column="name", pattern="(unclosed")


def test_drop_duplicates_keeps_the_first_of_each_key():
    rows = run(
        "drop_duplicates",
        [{"k": "a", "n": 1}, {"k": "a", "n": 2}, {"k": "b", "n": 3}],
        columns=["k"],
    )
    assert [row["n"] for row in rows] == [1, 3]


def test_the_api_publishes_the_vocabulary(client, api):
    body = client.get(f"{api}/transforms").json()
    keys = {entry["key"] for entry in body["transforms"]}
    assert {"cast_types", "bin_numeric", "flag_rows", "summarise"} <= keys
    assert len(keys) >= 20


def test_transforms_chain_into_a_pipeline_shaped_run(client, api):
    """The point of the vocabulary: steps compose without code between them."""
    rows = RAW
    for key, options in [
        ("parse_numeric", {"column": "wind", "output": "wind_ms"}),
        ("cast_types", {"casts": {"pressure": "number"}}),
        ("datetime_parts", {"column": "start", "prefix": "genesis", "parts": ["month"]}),
        ("duration_between", {"start": "start", "end": "end", "output": "hours"}),
        ("flag_rows", {"column": "landfall", "op": "not_empty", "output": "landed"}),
        (
            "bin_numeric",
            {
                "column": "wind_ms",
                "edges": [0, 32.7, 51.0, 120.0],
                "labels": ["mild", "moderate", "severe"],
                "output": "band",
            },
        ),
        ("drop_columns", {"columns": ["wind", "start", "end"]}),
    ]:
        rows = library.get(key).apply(Table.from_rows(rows), options).to_rows()

    assert len(rows) == 3
    assert set(rows[0]) == {
        "name", "pressure", "landfall", "code",
        "wind_ms", "genesis_month", "hours", "landed", "band",
    }
    assert [row["band"] for row in rows] == ["mild", "severe", "mild"]
