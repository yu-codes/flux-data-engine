"""Pipelines: a graph of executions threading datasets from step to step."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

ROWS = [
    {"product": "Widget", "price": 25.0, "quantity": 4},
    {"product": "Gadget", "price": 60.0, "quantity": 31},
    {"product": "Widget", "price": 24.0, "quantity": 12},
    {"product": "Gadget", "price": 61.0, "quantity": 7},
    {"product": "Doohickey", "price": 12.0, "quantity": 44},
]


@pytest.fixture(scope="module")
def dataset(client, api) -> dict:
    relative = "Demo/sources/test_pipeline.csv"
    path = Path(get_settings().data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    source = client.post(
        f"{api}/sources",
        json={"name": "pipeline sample", "type": "csv", "connection": {"path": relative}},
    ).json()
    return client.post(
        f"{api}/datasets", json={"name": "Pipeline sample", "source_id": source["id"]}
    ).json()


@pytest.fixture(scope="module")
def models(client, api) -> dict:
    revenue = client.post(
        f"{api}/models",
        json={
            "name": "Pipeline revenue",
            "provider": "formula",
            "configuration": {"expressions": {"revenue": "price * quantity"}},
        },
    )
    assert revenue.status_code == 201, revenue.text

    classify = client.post(
        f"{api}/models",
        json={
            "name": "Pipeline order class",
            "provider": "rule",
            "configuration": {
                "rules": [
                    {"name": "large", "when": "revenue >= 500",
                     "then": {"order_class": "LARGE"}}
                ],
                "default": {"order_class": "SMALL"},
            },
        },
    )
    assert classify.status_code == 201, classify.text

    rolling = client.post(
        f"{api}/models",
        json={
            "name": "Pipeline rolling revenue",
            "provider": "python-transform",
            "configuration": {
                "transform": "moving_average",
                "options": {"column": "revenue", "window": 2},
            },
        },
    )
    assert rolling.status_code == 201, rolling.text
    return {
        "revenue": revenue.json(),
        "classify": classify.json(),
        "rolling": rolling.json(),
    }


# --------------------------------------------------------------------------
# graph validation
# --------------------------------------------------------------------------
def test_a_cycle_is_rejected(client, api, dataset, models):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Cyclic pipeline",
            "input_dataset_id": dataset["id"],
            "steps": [
                {"name": "a", "model_id": models["revenue"]["id"], "input_from": "b"},
                {"name": "b", "model_id": models["classify"]["id"], "input_from": "a"},
            ],
        },
    )
    assert response.status_code == 422
    assert "cycle" in response.json()["message"]


def test_an_unknown_upstream_step_is_rejected(client, api, dataset, models):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Dangling pipeline",
            "input_dataset_id": dataset["id"],
            "steps": [
                {"name": "a", "model_id": models["revenue"]["id"], "input_from": "nope"}
            ],
        },
    )
    assert response.status_code == 422
    assert "not a step in this pipeline" in response.json()["message"]


def test_duplicate_step_names_are_rejected(client, api, dataset, models):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Duplicate pipeline",
            "input_dataset_id": dataset["id"],
            "steps": [
                {"name": "same", "model_id": models["revenue"]["id"]},
                {"name": "same", "model_id": models["classify"]["id"]},
            ],
        },
    )
    assert response.status_code == 422
    assert "duplicate step name" in response.json()["message"]


# --------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def pipeline(client, api, dataset, models) -> dict:
    """revenue -> {order class, rolling average}: one branch point."""
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Revenue then classify",
            "description": "Compute revenue, then branch into rules and a transform",
            "input_dataset_id": dataset["id"],
            "steps": [
                {"name": "revenue", "model_id": models["revenue"]["id"]},
                {
                    "name": "classify",
                    "model_id": models["classify"]["id"],
                    "input_from": "revenue",
                },
                {
                    "name": "rolling",
                    "model_id": models["rolling"]["id"],
                    "input_from": "revenue",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_graph_describes_the_branch(client, api, pipeline, dataset):
    graph = client.get(f"{api}/pipelines/{pipeline['id']}/graph").json()
    assert {n["id"] for n in graph["nodes"]} == {
        "__input__", "revenue", "classify", "rolling"
    }
    assert {(e["from"], e["to"]) for e in graph["edges"]} == {
        ("__input__", "revenue"), ("revenue", "classify"), ("revenue", "rolling")
    }
    #  Nothing consumes classify or rolling, so they are the outputs.
    assert set(graph["terminal_steps"]) == {"classify", "rolling"}


def test_running_threads_tables_from_step_to_step(client, api, pipeline):
    """Every step runs; only the ends of the run become datasets.

    An intermediate is working state. It used to be published as a Dataset
    with a name and a place in the catalogue, which is why a twelve-step
    pipeline added twelve datasets nobody had asked for, and why two enum
    values existed to hide them again afterwards.
    """
    run = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={})
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "succeeded", body["error"]
    assert len(body["step_runs"]) == 3

    by_name = {s["step_name"]: s for s in body["step_runs"]}
    #  Dependency order, not declaration order.
    assert by_name["revenue"]["order"] == 0
    for step in by_name.values():
        assert step["status"] == "succeeded", step["error"]
        assert step["execution_id"]
        #  Every step produces a result: that is the record of what it did.
        assert step["result_id"], f"{step['step_name']} produced no result"
        assert step["row_count"] == len(ROWS)

    #  "revenue" feeds two other steps, so it is a checkpoint, not a dataset.
    assert not by_name["revenue"]["dataset_version_id"]
    #  The two leaves are what the pipeline was built to produce.
    assert by_name["classify"]["dataset_version_id"]
    assert by_name["rolling"]["dataset_version_id"]
    assert len(body["output_dataset_ids"]) == 2


def test_downstream_steps_see_upstream_columns(client, api, pipeline):
    run = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={}).json()
    classify = next(s for s in run["step_runs"] if s["step_name"] == "classify")

    preview = client.get(
        f"{api}/dataset-versions/{classify['dataset_version_id']}/preview?limit=10"
    ).json()
    columns = {c["name"] for c in preview["columns"]}
    #  revenue came from the first step; order_class from this one.
    assert {"product", "price", "quantity", "revenue", "order_class"} <= columns

    classes = {row["order_class"] for row in preview["rows"]}
    assert classes == {"LARGE", "SMALL"}


def test_each_step_is_an_ordinary_execution(client, api, pipeline):
    run = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={}).json()
    step = run["step_runs"][0]

    execution = client.get(f"{api}/executions/{step['execution_id']}").json()
    assert execution["status"] == "succeeded"
    #  The pipeline records itself in the execution's context, so a run is
    #  traceable from either direction.
    assert execution["context"]["pipeline_id"] == pipeline["id"]
    assert execution["context"]["step"] == step["step_name"]


def test_a_failing_step_stops_its_branch_and_is_reported(client, api, dataset, models):
    """A broken step fails, its dependants are cancelled, the run is recorded."""
    broken = client.post(
        f"{api}/models",
        json={
            "name": "Pipeline broken step",
            "provider": "python-transform",
            "configuration": {
                "transform": "zscore_outliers",
                #  This column does not exist in the upstream dataset.
                "options": {"column": "not_a_column"},
            },
        },
    ).json()

    pipeline = client.post(
        f"{api}/pipelines",
        json={
            "name": "Pipeline with a broken step",
            "input_dataset_id": dataset["id"],
            "steps": [
                {"name": "revenue", "model_id": models["revenue"]["id"]},
                {"name": "boom", "model_id": broken["id"], "input_from": "revenue"},
                {"name": "after", "model_id": models["classify"]["id"],
                 "input_from": "boom"},
            ],
        },
    ).json()

    run = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={}).json()
    by_name = {s["step_name"]: s for s in run["step_runs"]}

    assert by_name["revenue"]["status"] == "succeeded"
    assert by_name["boom"]["status"] == "failed"
    assert by_name["boom"]["error"]
    #  Nothing downstream of a failed step runs.
    assert by_name["after"]["status"] == "cancelled"
    assert run["status"] == "failed"
    assert run["error"]
    #  The failure is data, not an exception at the caller.
    assert run["finished_at"]


def test_runs_are_listed_for_the_pipeline(client, api, pipeline):
    runs = client.get(f"{api}/pipeline-runs?pipeline_id={pipeline['id']}").json()
    assert runs, "the pipeline has been run several times by now"
    assert all(r["pipeline_id"] == pipeline["id"] for r in runs)

    detail = client.get(f"{api}/pipeline-runs/{runs[0]['id']}").json()
    assert detail["id"] == runs[0]["id"]
    assert detail["duration_seconds"] is not None


def test_a_pipeline_completes_even_when_executions_are_queued(client, api, pipeline):
    """A pipeline run is one unit of work, whatever the deployment's mode.

    In queue mode a single execution comes back pending; a pipeline cannot work
    that way, because each step's output is the next step's input.
    """
    from app.core.container import build_services
    from app.core.database import session_scope

    class NeverRuns:
        runs_inline = False
        mode = "queue"

        def enqueue(self, execution_id: str) -> None:
            """A worker that is not there."""

    with session_scope() as session:
        services = build_services(session)
        #  Simulate queue mode: single executions would be handed off.
        services.executions.dispatcher = NeverRuns()
        run = services.pipelines.run(pipeline["id"])

    assert run.status.value == "succeeded", run.error
    assert all(step.status.value == "succeeded" for step in run.step_runs)
    #  Every step recorded a result; the terminal ones also produced datasets.
    assert all(step.result_id for step in run.step_runs)
    assert sum(1 for step in run.step_runs if step.dataset_version_id) == 2


# --------------------------------------------------------------------------
# what a run ran
# --------------------------------------------------------------------------
def test_a_run_still_says_what_it_ran_after_the_pipeline_changes(
    client, api, dataset
):
    """The bug a ModelVersion snapshot already fixed, one level up.

    A pipeline run recorded which pipeline it belonged to and nothing about
    what that pipeline was. Edit a step afterwards and every past run silently
    starts describing itself with the new steps - the record and the thing it
    records had no distance between them.
    """
    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Snapshot demo",
            "input_dataset_id": dataset["id"],
            "steps": [
                {
                    "name": "keep the cheap ones",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "filter_rows",
                        "options": {"column": "price", "op": "lt", "value": 50},
                    },
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    pipeline_id = created.json()["id"]

    run = client.post(f"{api}/pipelines/{pipeline_id}/run", json={})
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]

    #  Now change the pipeline out from under the run.
    changed = client.patch(
        f"{api}/pipelines/{pipeline_id}",
        json={
            "steps": [
                {
                    "name": "keep the dear ones",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "filter_rows",
                        "options": {"column": "price", "op": "gt", "value": 50},
                    },
                }
            ]
        },
    )
    assert changed.status_code == 200, changed.text

    recorded = client.get(f"{api}/pipeline-runs/{run_id}")
    assert recorded.status_code == 200, recorded.text
    snapshot = recorded.json()["definition_snapshot"]

    assert [s["name"] for s in snapshot["steps"]] == ["keep the cheap ones"]
    assert snapshot["steps"][0]["configuration"]["options"]["op"] == "lt"
