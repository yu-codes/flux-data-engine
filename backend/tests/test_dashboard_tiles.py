"""A dashboard is a working surface, not a fixed publication.

Charts have to be addable, removable and reorderable after the dashboard
exists, and the grid has to re-flow so removing one never leaves a hole.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

ROWS = [
    {"month": 7, "band": "mild", "rain": 40},
    {"month": 7, "band": "severe", "rain": 300},
    {"month": 8, "band": "mild", "rain": 60},
    {"month": 8, "band": "severe", "rain": 520},
]


@pytest.fixture(scope="module")
def version_id(client, api) -> str:
    settings = get_settings()
    relative = "Demo/sources/test_tiles.csv"
    path = Path(settings.data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    source = client.post(
        f"{api}/sources",
        json={"name": "tile rows", "type": "csv", "connection": {"path": relative}},
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets", json={"name": "Tile rows", "source_id": source.json()["id"]}
    )
    assert dataset.status_code == 201, dataset.text
    return dataset.json()["versions"][0]["id"]


@pytest.fixture(scope="module")
def charts(client, api, version_id) -> list[str]:
    ids = []
    for index, kind in enumerate(["bar", "histogram", "box", "heatmap"]):
        spec = {"chart_type": kind, "y": ["rain"]}
        if kind != "histogram":
            spec["x"] = "band"
        if kind == "heatmap":
            spec["x"] = "month"
            spec["series"] = "band"
            spec["aggregation"] = "mean"
        created = client.post(
            f"{api}/visualizations",
            json={
                "name": f"Tile chart {index} ({kind})",
                "dataset_version_id": version_id,
                "spec": spec,
            },
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["id"])
    return ids


@pytest.fixture(scope="module")
def dashboard(client, api, charts) -> str:
    created = client.post(
        f"{api}/dashboards",
        json={
            "name": "Tile lifecycle",
            "tiles": [
                {"visualization_id": charts[0], "x": 0, "y": 0, "width": 6, "height": 4}
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def test_a_chart_can_be_added_after_the_dashboard_exists(client, api, dashboard, charts):
    added = client.post(
        f"{api}/dashboards/{dashboard}/tiles", json={"visualization_id": charts[1]}
    )
    assert added.status_code == 201, added.text
    tiles = added.json()["tiles"]
    assert len(tiles) == 2
    #  Second tile sits beside the first, because both fit on one row.
    assert (tiles[1]["x"], tiles[1]["y"]) == (6, 0)


def test_a_third_chart_wraps_onto_the_next_row(client, api, dashboard, charts):
    added = client.post(
        f"{api}/dashboards/{dashboard}/tiles", json={"visualization_id": charts[2]}
    )
    assert added.status_code == 201, added.text
    third = added.json()["tiles"][2]
    assert (third["x"], third["y"]) == (0, 4)


def test_the_same_chart_cannot_be_added_twice(client, api, dashboard, charts):
    duplicate = client.post(
        f"{api}/dashboards/{dashboard}/tiles", json={"visualization_id": charts[1]}
    )
    assert duplicate.status_code == 409


def test_a_missing_chart_is_refused_before_it_can_break_rendering(client, api, dashboard):
    missing = client.post(
        f"{api}/dashboards/{dashboard}/tiles", json={"visualization_id": "viz_nope"}
    )
    assert missing.status_code == 404


def test_a_tile_can_be_widened(client, api, dashboard, charts):
    widened = client.patch(
        f"{api}/dashboards/{dashboard}/tiles/{charts[1]}", json={"width": 12}
    )
    assert widened.status_code == 200, widened.text
    tile = next(t for t in widened.json()["tiles"] if t["visualization_id"] == charts[1])
    assert tile["width"] == 12


def test_a_tile_can_be_moved_in_reading_order(client, api, dashboard, charts):
    moved = client.patch(
        f"{api}/dashboards/{dashboard}/tiles/{charts[1]}", json={"move": -1}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["tiles"][0]["visualization_id"] == charts[1]


def test_removing_a_tile_reflows_the_grid(client, api, dashboard, charts):
    removed = client.delete(f"{api}/dashboards/{dashboard}/tiles/{charts[1]}")
    assert removed.status_code == 200, removed.text
    tiles = removed.json()["tiles"]
    assert len(tiles) == 2
    #  No hole: the survivors close up onto the first row.
    assert [(t["x"], t["y"]) for t in tiles] == [(0, 0), (6, 0)]


def test_removing_a_tile_that_is_not_there_is_a_404(client, api, dashboard, charts):
    gone = client.delete(f"{api}/dashboards/{dashboard}/tiles/{charts[1]}")
    assert gone.status_code == 404


def test_the_dashboard_still_renders_every_remaining_tile(client, api, dashboard):
    rendered = client.get(f"{api}/dashboards/{dashboard}/render")
    assert rendered.status_code == 200, rendered.text
    tiles = rendered.json()["tiles"]
    assert len(tiles) == 2
    for tile in tiles:
        assert not tile["chart"].get("error"), tile["chart"]
        assert tile["chart"]["series"]
