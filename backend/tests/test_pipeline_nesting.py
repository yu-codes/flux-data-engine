"""A pipeline can be a step of another pipeline.

The alternative is copying: a shared five-step preparation pasted into every
pipeline that needs it, after which fixing it means finding all the copies and
nobody is sure they found them all. A pipeline is a runnable, and a runnable
that other things can be built from is what makes it one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def dataset(client, api) -> dict:
    source = client.post(
        f"{api}/sources",
        json={
            "name": "nested rows",
            "type": "inline",
            "connection": {
                "rows": [
                    {"city": "Taipei", "units": 3},
                    {"city": "Tainan", "units": 9},
                    {"city": "Taichung", "units": 1},
                ]
            },
        },
    )
    assert source.status_code == 201, source.text
    created = client.post(
        f"{api}/datasets",
        json={"name": "Nested data", "source_id": source.json()["id"]},
    )
    assert created.status_code == 201, created.text
    return created.json()


def _filter_step(name: str, threshold: int) -> dict:
    return {
        "name": name,
        "provider": "python-transform",
        "configuration": {
            "transform": "filter_rows",
            "options": {"column": "units", "op": "gt", "value": threshold},
        },
    }


@pytest.fixture(scope="module")
def shared(client, api, dataset) -> dict:
    """The pipeline everything else nests: one shared preparation."""
    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Shared preparation",
            "input_dataset_id": dataset["id"],
            "steps": [_filter_step("drop the quiet ones", 2)],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_step_can_run_another_pipeline(client, api, dataset, shared):
    outer = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nests the shared one",
            "input_dataset_id": dataset["id"],
            "steps": [
                {"name": "prepare", "pipeline_id": shared["id"]},
                {
                    "name": "then narrow further",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "filter_rows",
                        "options": {"column": "units", "op": "gt", "value": 5},
                    },
                    "input_from": "prepare",
                },
            ],
        },
    )
    assert outer.status_code == 201, outer.text

    run = client.post(f"{api}/pipelines/{outer.json()['id']}/run", json={})
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "succeeded", body.get("error")

    nested_step = next(s for s in body["step_runs"] if s["step_name"] == "prepare")
    #  The nested run is a run of its own, not steps flattened into this one.
    assert nested_step["pipeline_run_id"], nested_step
    nested = client.get(f"{api}/pipeline-runs/{nested_step['pipeline_run_id']}")
    assert nested.status_code == 200, nested.text
    assert nested.json()["pipeline_id"] == shared["id"]

    #  The outer pipeline's answer went through both filters.
    dataset_id = body["output_dataset_ids"][0]
    rows = client.get(f"{api}/datasets/{dataset_id}/preview").json()["rows"]
    assert {row["city"] for row in rows} == {"Tainan"}


def test_a_nested_pipeline_that_fails_fails_the_outer_step(client, api, dataset):
    inner = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nested and broken",
            "input_dataset_id": dataset["id"],
            "steps": [
                {
                    "name": "filter a column that is not there",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "filter_rows",
                        "options": {"column": "nope", "op": "gt", "value": 1},
                    },
                }
            ],
        },
    )
    assert inner.status_code == 201, inner.text

    outer = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nests the broken one",
            "input_dataset_id": dataset["id"],
            "steps": [{"name": "prepare", "pipeline_id": inner.json()["id"]}],
        },
    )
    assert outer.status_code == 201, outer.text

    run = client.post(f"{api}/pipelines/{outer.json()['id']}/run", json={})
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "failed"
    assert run.json()["step_runs"][0]["pipeline_run_id"]


def test_invoking_a_pipeline_runs_its_nested_pipelines_too(
    client, api, dataset, shared
):
    outer = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nests the shared one for serving",
            "input_dataset_id": dataset["id"],
            "steps": [{"name": "prepare", "pipeline_id": shared["id"]}],
        },
    )
    assert outer.status_code == 201, outer.text

    answered = client.post(
        f"{api}/pipelines/{outer.json()['id']}/invoke",
        json={"rows": [{"city": "Hualien", "units": 7}, {"city": "Yilan", "units": 1}]},
    )
    assert answered.status_code == 200, answered.text
    #  The rows the caller brought reached the nested pipeline, which is the
    #  whole point: a shared preparation applied to data on the way in.
    assert {row["city"] for row in answered.json()["rows"]} == {"Hualien"}


def test_a_pipeline_cannot_nest_itself(client, api, dataset, shared):
    refused = client.patch(
        f"{api}/pipelines/{shared['id']}",
        json={"steps": [{"name": "itself", "pipeline_id": shared["id"]}]},
    )
    assert refused.status_code == 422, refused.text
    assert "never end" in refused.text


def test_two_pipelines_cannot_nest_each_other(client, api, dataset, shared):
    """The loop that is not obvious while editing either half of it."""
    middle = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nests the shared one, and is nested back",
            "input_dataset_id": dataset["id"],
            "steps": [{"name": "prepare", "pipeline_id": shared["id"]}],
        },
    )
    assert middle.status_code == 201, middle.text

    refused = client.patch(
        f"{api}/pipelines/{shared['id']}",
        json={"steps": [{"name": "back again", "pipeline_id": middle.json()["id"]}]},
    )
    assert refused.status_code == 422, refused.text
    assert "never end" in refused.text


def test_a_step_that_names_a_missing_pipeline_is_refused_while_editing(
    client, api, dataset
):
    refused = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nests a ghost",
            "input_dataset_id": dataset["id"],
            "steps": [{"name": "prepare", "pipeline_id": "pipe_nope"}],
        },
    )
    assert refused.status_code == 422, refused.text
    assert "does not exist" in refused.text


def test_a_step_runs_one_thing(client, api, dataset, shared):
    """Two answers to "what does this step run" is not a preference."""
    refused = client.post(
        f"{api}/pipelines",
        json={
            "name": "Names two things",
            "input_dataset_id": dataset["id"],
            "steps": [
                {
                    "name": "confused",
                    "pipeline_id": shared["id"],
                    "provider": "python-transform",
                    "configuration": {"transform": "filter_rows"},
                }
            ],
        },
    )
    assert refused.status_code == 422, refused.text
    assert "runs one thing" in refused.text


def test_the_graph_shows_a_nested_step_for_what_it_is(client, api, dataset, shared):
    outer = client.post(
        f"{api}/pipelines",
        json={
            "name": "Nests the shared one, drawn",
            "input_dataset_id": dataset["id"],
            "steps": [{"name": "prepare", "pipeline_id": shared["id"]}],
        },
    )
    assert outer.status_code == 201, outer.text

    graph = client.get(f"{api}/pipelines/{outer.json()['id']}/graph")
    assert graph.status_code == 200, graph.text
    node = next(n for n in graph.json()["nodes"] if n["id"] == "prepare")
    assert node["type"] == "pipeline"
    assert node["model_name"] == "Shared preparation"
