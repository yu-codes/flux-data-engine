"""Explore asks the data a question instead of reading all of it first.

Correct answers are not enough here: the old implementation also returned the
right page, it just built every row of the dataset to do it. So these tests
measure the work, not only the result - they count the rows that actually get
materialised as Python dicts, and the columns that get read off disk.

Without this, "we made it push down" is an unverifiable claim that quietly
stops being true the first time somebody adds a `to_rows()` back.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.shared.tabular import Table

ROW_COUNT = 4000


@pytest.fixture(scope="module")
def big_dataset(client, api) -> dict:
    """Wide enough that projection matters, long enough that paging does."""
    relative = "samples/test_pushdown.csv"
    path = Path(get_settings().data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)

    #  Two categorical columns, because a cohort chart needs both an axis and
    #  a band to split by.
    columns = ["city", "channel", "units", "price"] + [f"extra_{i}" for i in range(20)]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for n in range(ROW_COUNT):
            row = {f"extra_{i}": f"padding-{n}-{i}" for i in range(20)}
            row.update(
                {
                    "city": ["Taipei", "Tainan", "Taichung"][n % 3],
                    "channel": ["online", "store"][n % 2],
                    "units": n % 97,
                    "price": round(10 + (n % 53) * 1.5, 2),
                }
            )
            writer.writerow(row)

    source = client.post(
        f"{api}/sources",
        json={"name": "pushdown source", "type": "csv", "connection": {"path": relative}},
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Pushdown sample", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text
    return dataset.json()


@pytest.fixture
def materialised(monkeypatch) -> list[int]:
    """Record how many rows each `to_rows()` call turns into dicts."""
    sizes: list[int] = []
    original = Table.to_rows

    def counting(self, limit=None, offset=0):
        rows = original(self, limit=limit, offset=offset)
        sizes.append(len(rows))
        return rows

    monkeypatch.setattr(Table, "to_rows", counting)
    return sizes


@pytest.fixture
def columns_read(monkeypatch) -> list[list[str] | None]:
    """Record which columns each Parquet read asks for."""
    asked: list[list[str] | None] = []
    original = Table.from_parquet

    def watching(path, columns=None):
        asked.append(list(columns) if columns else None)
        return original(path, columns=columns)

    monkeypatch.setattr(Table, "from_parquet", watching)
    return asked


# --------------------------------------------------------------------------
# paging
# --------------------------------------------------------------------------
def test_a_page_of_twenty_materialises_twenty_rows(
    client, api, big_dataset, materialised
):
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/query", json={"limit": 20, "offset": 0}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["rows"]) == 20
    assert body["total"] == ROW_COUNT
    #  The point of the whole exercise: asking for twenty rows costs twenty
    #  rows, not four thousand.
    assert max(materialised) <= 20, (
        f"a 20-row page materialised {max(materialised)} rows"
    )


def test_paging_with_a_filter_and_a_sort_still_pages(
    client, api, big_dataset, materialised
):
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/query",
        json={
            "filters": [{"column": "city", "op": "eq", "value": "Taipei"}],
            "sort_by": "units",
            "sort_desc": True,
            "limit": 25,
            "offset": 50,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["rows"]) == 25
    assert body["total"] < ROW_COUNT           # the filter really filtered
    assert all(row["city"] == "Taipei" for row in body["rows"])
    #  Descending, and nulls would be last if there were any.
    units = [row["units"] for row in body["rows"]]
    assert units == sorted(units, reverse=True)
    assert max(materialised) <= 25


def test_the_last_page_is_short_not_wrong(client, api, big_dataset):
    version = big_dataset["current_version_id"]
    body = client.post(
        f"{api}/explore/{version}/query",
        json={"limit": 100, "offset": ROW_COUNT - 30},
    ).json()
    assert len(body["rows"]) == 30
    assert body["total"] == ROW_COUNT


def test_offsets_past_the_end_return_nothing_rather_than_failing(client, api, big_dataset):
    version = big_dataset["current_version_id"]
    body = client.post(
        f"{api}/explore/{version}/query", json={"limit": 10, "offset": ROW_COUNT + 500}
    ).json()
    assert body["rows"] == []
    assert body["total"] == ROW_COUNT


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------
def test_asking_for_two_columns_returns_two_columns(client, api, big_dataset):
    version = big_dataset["current_version_id"]
    body = client.post(
        f"{api}/explore/{version}/query",
        json={"columns": ["city", "units"], "limit": 5},
    ).json()

    assert all(set(row) == {"city", "units"} for row in body["rows"])
    #  The full schema is still reported: the column picker has to be able to
    #  offer the columns that were not selected.
    assert {f["name"] for f in body["columns"]} > {"city", "units"}


def test_a_projection_still_filters_on_a_column_it_does_not_return(
    client, api, big_dataset
):
    """A column can be needed to answer the question without being in the answer."""
    version = big_dataset["current_version_id"]
    body = client.post(
        f"{api}/explore/{version}/query",
        json={
            "columns": ["city"],
            "filters": [{"column": "units", "op": "lt", "value": 5}],
            "limit": 10,
        },
    ).json()

    assert body["total"] > 0
    assert all(set(row) == {"city"} for row in body["rows"])


# --------------------------------------------------------------------------
# charts
# --------------------------------------------------------------------------
def test_a_histogram_never_builds_a_row(client, api, big_dataset, materialised):
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "histogram", "y": ["price"], "bins": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["categories"]) == 10
    assert sum(body["series"][0]["data"]) == ROW_COUNT
    #  A distribution reads one column as numbers; it has no use for rows.
    assert materialised == [] or max(materialised) == 0


def test_an_aggregated_chart_materialises_groups_not_rows(
    client, api, big_dataset, materialised
):
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "bar", "x": "city", "y": ["units"], "aggregation": "sum"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert sorted(body["categories"]) == ["Taichung", "Tainan", "Taipei"]
    assert body["row_count"] == ROW_COUNT
    #  Three groups come back as three rows, not four thousand.
    assert max(materialised) <= 3


def test_aggregates_still_add_up(client, api, big_dataset):
    """Pushing the sum into Arrow must not change the sum."""
    version = big_dataset["current_version_id"]
    body = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "bar", "x": "city", "y": ["units"], "aggregation": "sum"},
    ).json()

    expected = sum(n % 97 for n in range(ROW_COUNT))
    assert sum(body["series"][0]["data"]) == pytest.approx(expected)


def test_counting_counts_rows_including_ones_with_no_value(client, api, big_dataset):
    version = big_dataset["current_version_id"]
    body = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "bar", "x": "city", "y": ["units"], "aggregation": "count"},
    ).json()
    assert sum(body["series"][0]["data"]) == ROW_COUNT


def test_a_box_plot_reads_two_columns_not_four_thousand_rows(
    client, api, big_dataset, materialised
):
    """The last chart shape that still built rows.

    A box plot needs a measure and the category it belongs to. It was reading
    every row of the dataset to get them, which is the same cost the whole
    rewrite was about - just further from the front page.
    """
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "box", "x": "city", "y": ["units"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert sorted(body["categories"]) == ["Taichung", "Tainan", "Taipei"]
    assert {s["name"] for s in body["series"]} == {"min", "q1", "median", "q3", "max"}
    assert sum(body["group_sizes"]) == ROW_COUNT
    assert materialised == [] or max(materialised) == 0


def test_a_cohort_chart_reads_three_columns_not_four_thousand_rows(
    client, api, big_dataset, materialised
):
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/series",
        json={
            "chart_type": "bar",
            "x": "city",
            "series": "channel",
            "y": ["units"],
            "aggregation": "sum",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["row_count"] == ROW_COUNT
    assert body["band_title"] == "channel"
    assert [s["name"] for s in body["series"]] == ["online", "store"]
    assert materialised == [] or max(materialised) == 0


def test_a_chart_reads_only_the_columns_it_names(client, api, big_dataset, columns_read):
    """A two-column chart over a twenty-four-column dataset.

    The narrowing happened after the file was read, so the projection saved
    Python work and no disk at all. Parquet is columnar: asking for two
    columns should read two columns.
    """
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "bar", "x": "city", "y": ["units"], "aggregation": "sum"},
    )
    assert response.status_code == 200, response.text

    assert columns_read, "the chart did not read a Parquet file at all"
    assert columns_read[-1] == ["city", "units"]


def test_a_chart_naming_a_column_that_is_gone_still_says_so(client, api, big_dataset):
    """Narrowing must not turn a bad column name into a failed read."""
    version = big_dataset["current_version_id"]
    response = client.post(
        f"{api}/explore/{version}/series",
        json={"chart_type": "bar", "x": "nope", "y": ["units"], "aggregation": "sum"},
    )
    assert response.status_code == 422, response.text
    assert "nope" in response.text
