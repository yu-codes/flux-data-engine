"""Distribution, grid and cohort charts.

A bar chart answers "how much per category". These answer different questions,
and each has a shape the renderer depends on, so the shape is pinned here.
"""

from __future__ import annotations

import pytest

from app.modules.analysis.application.services import build_series
from app.modules.analysis.domain.entities import ChartSpec
from app.shared.tabular import Table

ROWS = [
    {"band": "mild", "season": "summer", "wind": 20.0, "rain": 10.0},
    {"band": "mild", "season": "summer", "wind": 22.0, "rain": 20.0},
    {"band": "mild", "season": "autumn", "wind": 24.0, "rain": 30.0},
    {"band": "moderate", "season": "summer", "wind": 40.0, "rain": 120.0},
    {"band": "moderate", "season": "summer", "wind": 44.0, "rain": 140.0},
    {"band": "moderate", "season": "autumn", "wind": 48.0, "rain": 900.0},
    {"band": "severe", "season": "summer", "wind": 60.0, "rain": 200.0},
    {"band": "severe", "season": "autumn", "wind": 70.0, "rain": 260.0},
]


@pytest.fixture()
def table() -> Table:
    return Table.from_rows(ROWS)


def test_histogram_buckets_a_column_and_summarises_it(table):
    chart = build_series(
        table, ChartSpec.from_dict({"chart_type": "histogram", "y": ["wind"], "bins": 5})
    )

    assert chart["chart_type"] == "histogram"
    assert len(chart["categories"]) == 5
    #  Every row is counted exactly once, including the one on the top edge.
    assert sum(chart["series"][0]["data"]) == len(ROWS)

    summary = chart["distribution"]
    assert summary["min"] == 20.0
    assert summary["max"] == 70.0
    assert summary["counted"] == len(ROWS)
    #  The axis is the column being distributed, not a category column.
    assert chart["x_title"] == "wind"
    assert chart["y_title"] == "rows"


def test_histogram_of_a_constant_column_is_one_bucket(table):
    flat = Table.from_rows([{"v": 5.0} for _ in range(4)])
    chart = build_series(flat, ChartSpec.from_dict({"chart_type": "histogram", "y": ["v"]}))
    assert chart["series"][0]["data"] == [4.0]


def test_box_reports_the_five_number_summary_per_group(table):
    chart = build_series(
        table,
        ChartSpec.from_dict(
            {
                "chart_type": "box",
                "x": "band",
                "y": ["wind"],
                "x_order": ["mild", "moderate", "severe"],
            }
        ),
    )

    assert chart["categories"] == ["mild", "moderate", "severe"]
    names = [s["name"] for s in chart["series"]]
    assert names == ["min", "q1", "median", "q3", "max"]
    assert chart["group_sizes"] == [3, 3, 2]

    medians = next(s for s in chart["series"] if s["name"] == "median")
    assert medians["data"] == [22.0, 44.0, 65.0]

    #  A box plot never aggregates, so the y title names the column itself.
    assert chart["y_title"] == "wind"


def test_box_separates_outliers_from_the_whiskers():
    #  One value far outside 1.5 IQR: the whisker must not stretch to reach it.
    rows = [{"g": "a", "v": float(v)} for v in (10, 11, 12, 13, 14, 200)]
    spec = ChartSpec.from_dict({"chart_type": "box", "x": "g", "y": ["v"]})
    chart = build_series(Table.from_rows(rows), spec)
    top = next(s for s in chart["series"] if s["name"] == "max")
    assert top["data"][0] == 14.0
    assert chart["outliers"] == [{"category": "a", "value": 200.0}]


def test_heatmap_is_a_grid_of_x_by_series(table):
    chart = build_series(
        table,
        ChartSpec.from_dict(
            {
                "chart_type": "heatmap",
                "x": "season",
                "series": "band",
                "y": ["wind"],
                "aggregation": "count",
                "series_order": ["mild", "moderate", "severe"],
            }
        ),
    )

    assert chart["categories"] == ["autumn", "summer"]
    assert [s["name"] for s in chart["series"]] == ["mild", "moderate", "severe"]
    assert chart["band_title"] == "band"
    #  autumn/summer counts, per band.
    assert [s["data"] for s in chart["series"]] == [[1.0, 2.0], [1.0, 2.0], [1.0, 1.0]]


def test_heatmap_without_a_series_column_is_refused(table):
    from app.shared.errors import ValidationError

    with pytest.raises(ValidationError):
        build_series(
            table,
            ChartSpec.from_dict({"chart_type": "heatmap", "x": "season", "y": ["wind"]}),
        )


def test_a_series_column_splits_any_chart_into_cohorts(table):
    chart = build_series(
        table,
        ChartSpec.from_dict(
            {
                "chart_type": "stacked_bar",
                "x": "season",
                "series": "band",
                "y": ["rain"],
                "aggregation": "mean",
                "series_order": ["mild", "moderate", "severe"],
            }
        ),
    )
    assert [s["name"] for s in chart["series"]] == ["mild", "moderate", "severe"]
    #  autumn then summer; mild autumn is the single 30 mm reading.
    assert chart["series"][0]["data"] == [30.0, 15.0]


def test_stated_order_wins_over_alphabetical(table):
    """An intensity scale is ordinal; sorting it as text puts moderate first."""
    natural = build_series(
        table,
        ChartSpec.from_dict(
            {"chart_type": "bar", "x": "band", "y": ["wind"], "aggregation": "mean"}
        ),
    )
    assert natural["categories"] == ["mild", "moderate", "severe"]

    stated = build_series(
        table,
        ChartSpec.from_dict(
            {
                "chart_type": "bar",
                "x": "band",
                "y": ["wind"],
                "aggregation": "mean",
                "x_order": ["severe", "mild", "moderate"],
            }
        ),
    )
    assert stated["categories"] == ["severe", "mild", "moderate"]


def test_unlisted_categories_keep_their_place_after_the_stated_ones(table):
    chart = build_series(
        table,
        ChartSpec.from_dict(
            {
                "chart_type": "bar",
                "x": "band",
                "y": ["wind"],
                "aggregation": "count",
                "x_order": ["severe"],
            }
        ),
    )
    assert chart["categories"] == ["severe", "mild", "moderate"]


def test_every_chart_type_is_offered_by_the_api(client, api):
    kinds = set(client.get(f"{api}/chart-options").json()["chart_types"])
    assert {"histogram", "box", "heatmap", "stacked_bar"} <= kinds


def test_filters_match_a_typed_value_against_either_type():
    """The filter value comes from a text box; the column may hold either.

    Filtering moved into Arrow, which is strictly typed and will not compare a
    string with an integer at all - so the coercion this pins is what keeps a
    typed "1" finding a column of text and a typed "2" finding a column of
    numbers.
    """
    from app.shared.tabular import Table

    table = Table.from_rows([{"code": "1", "n": 1}, {"code": "2", "n": 2}])

    def codes(spec):
        return [row["code"] for row in table.filter([spec]).to_rows()]

    assert codes({"column": "code", "op": "eq", "value": 1}) == ["1"]
    assert codes({"column": "code", "op": "eq", "value": "1"}) == ["1"]
    assert codes({"column": "n", "op": "eq", "value": "2"}) == ["2"]
    assert codes({"column": "code", "op": "in", "value": [1, 2]}) == ["1", "2"]
    assert codes({"column": "code", "op": "ne", "value": 1}) == ["2"]


def test_an_unknown_operator_says_which_ones_exist():
    from app.shared.errors import ValidationError
    from app.shared.tabular import Table

    with pytest.raises(ValidationError) as raised:
        Table.from_rows([{"a": 1}]).filter(
            [{"column": "a", "op": "approximately", "value": 1}]
        )
    assert "operators" in raised.value.details
