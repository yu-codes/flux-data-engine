"""Composing a pipeline out of the standard vocabulary, the way the UI does.

The builder creates one Model per transform step, then chains them. Nothing in
that path needs code written for the occasion, which is the whole claim: the
same twelve verbs reshape any source.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

RAW = [
    {"name": "Alpha", "wind": "30 (m/s)", "start": "2020-07-01 08:00:00",
     "end": "2020-07-04 20:00:00", "landfall": "宜蘭"},
    {"name": "Bravo", "wind": "55 (m/s)", "start": "2021-08-11 00:00:00",
     "end": "2021-08-12 12:00:00", "landfall": ""},
    {"name": "Charlie", "wind": "18 (m/s)", "start": "2022-09-20 06:00:00",
     "end": "2022-09-22 06:00:00", "landfall": "花蓮"},
    {"name": "Delta", "wind": "62 (m/s)", "start": "2022-07-02 00:00:00",
     "end": "2022-07-09 00:00:00", "landfall": "台東"},
]

STEPS = [
    ("parse wind", "parse_numeric", {"column": "wind", "output": "wind_ms"}),
    ("derive season", "datetime_parts",
     {"column": "start", "prefix": "genesis", "parts": ["month"]}),
    ("derive lifetime", "duration_between",
     {"start": "start", "end": "end", "unit": "hours", "output": "lifetime_hours"}),
    ("flag landfall", "flag_rows",
     {"column": "landfall", "op": "not_empty", "output": "made_landfall"}),
    ("band intensity", "bin_numeric",
     {"column": "wind_ms", "edges": [0, 32.7, 51.0, 120.0],
      "labels": ["mild", "moderate", "severe"], "output": "band"}),
]


@pytest.fixture(scope="module")
def dataset_id(client, api) -> str:
    settings = get_settings()
    relative = "Demo/sources/test_composition.csv"
    path = Path(settings.data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RAW[0]))
        writer.writeheader()
        writer.writerows(RAW)

    source = client.post(
        f"{api}/sources",
        json={"name": "composition rows", "type": "csv", "connection": {"path": relative}},
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Composition rows", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text
    return dataset.json()["id"]


@pytest.fixture(scope="module")
def pipeline(client, api, dataset_id) -> dict:
    """Create a model per step, then the chain — exactly what the builder does."""
    chained = []
    previous = None
    for name, transform, options in STEPS:
        model = client.post(
            f"{api}/models",
            json={
                "name": f"Composition · {name}",
                "provider": "python-transform",
                "configuration": {"transform": transform, "options": options},
                "tags": ["pipeline"],
            },
        )
        assert model.status_code == 201, model.text
        chained.append(
            {"name": name, "model_id": model.json()["id"], "input_from": previous}
        )
        previous = name

    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Composed from transforms",
            "input_dataset_id": dataset_id,
            "steps": chained,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_the_chain_runs_and_carries_its_rows_all_the_way_through(client, api, pipeline):
    """Every step runs on every row; only the end of the chain is published.

    This used to assert a dataset per step, which is what the platform did and
    what made a long chain fill the catalogue with working state. What matters
    is that no rows are lost between steps, which is what `row_count` says.
    """
    run = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={})
    assert run.status_code in (200, 201), run.text
    body = run.json()
    assert body["status"] == "succeeded", body.get("error")

    steps = {s["step_name"]: s for s in body["step_runs"]}
    assert set(steps) == {name for name, _, _ in STEPS}
    for step in steps.values():
        assert step["status"] == "succeeded"
        assert step["result_id"], step
        assert step["row_count"] == len(RAW)

    #  One chain, one deliverable.
    published = [s for s in steps.values() if s["dataset_id"]]
    assert len(published) == 1
    assert published[0]["step_name"] == "band intensity"


def test_each_step_adds_exactly_what_it_promised(client, api, pipeline):
    runs = client.get(f"{api}/pipeline-runs?pipeline_id={pipeline['id']}").json()
    final = next(
        s for s in runs[0]["step_runs"] if s["step_name"] == "band intensity"
    )
    preview = client.get(
        f"{api}/dataset-versions/{final['dataset_version_id']}/preview?limit=10"
    )
    assert preview.status_code == 200, preview.text
    rows = preview.json()["rows"]

    columns = set(rows[0])
    derived = {"wind_ms", "genesis_month", "lifetime_hours", "made_landfall", "band"}
    assert derived <= columns

    by_name = {row["name"]: row for row in rows}
    assert by_name["Alpha"]["wind_ms"] == 30
    assert by_name["Alpha"]["genesis_month"] == 7
    assert by_name["Alpha"]["lifetime_hours"] == 84
    assert by_name["Bravo"]["made_landfall"] is False
    assert [by_name[n]["band"] for n in ("Alpha", "Bravo", "Charlie", "Delta")] == [
        "mild",
        "severe",
        "mild",
        "severe",
    ]


def test_the_final_table_can_be_charted_without_further_work(client, api, pipeline):
    """The point of the pipeline: what comes out is analysis-ready."""
    runs = client.get(f"{api}/pipeline-runs?pipeline_id={pipeline['id']}").json()
    final = next(s for s in runs[0]["step_runs"] if s["step_name"] == "band intensity")

    chart = client.post(
        f"{api}/explore/{final['dataset_version_id']}/series",
        json={
            "chart_type": "box",
            "x": "band",
            "y": ["lifetime_hours"],
            "x_order": ["mild", "moderate", "severe"],
        },
    )
    assert chart.status_code == 200, chart.text
    body = chart.json()
    assert body["categories"] == ["mild", "severe"]
    assert body["group_sizes"] == [2, 2]


def test_a_step_whose_parameters_do_not_fit_the_data_fails_that_step(
    client, api, dataset_id
):
    """A bad parameter must fail its own step with a readable reason."""
    model = client.post(
        f"{api}/models",
        json={
            "name": "Composition · impossible band",
            "provider": "python-transform",
            "configuration": {
                "transform": "bin_numeric",
                "options": {"column": "not_a_column", "edges": [0, 1]},
            },
        },
    )
    assert model.status_code == 201, model.text

    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Composed with a bad step",
            "input_dataset_id": dataset_id,
            "steps": [{"name": "band", "model_id": model.json()["id"]}],
        },
    )
    assert created.status_code == 201, created.text

    run = client.post(f"{api}/pipelines/{created.json()['id']}/run", json={})
    body = run.json()
    assert body["status"] == "failed"
    assert "not_a_column" in (body["error"] or "")
