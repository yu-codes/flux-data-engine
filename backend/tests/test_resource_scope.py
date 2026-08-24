"""A pipeline creates a pipeline, and nothing else.

This file used to pin two patches. A twelve-step pipeline created twelve
ModelDefinitions and twelve Datasets nobody had asked for, so `ModelScope.STEP`
hid the models and `DatasetOrigin.INTERMEDIATE` hid the datasets, and two
service passes ran afterwards to relabel what the run had just produced.

Both are gone, because the thing they were hiding is no longer created. A step
carries its own provider and configuration, and only what a pipeline was built
to produce becomes a Dataset. What is pinned here now is that invariant - a
run leaves behind exactly its outputs - because it is the kind of thing that
quietly stops being true.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

ROWS = [
    {"name": "Nari", "wind": "40 (m/s)", "landfall": "宜蘭"},
    {"name": "Morakot", "wind": "45 (m/s)", "landfall": "花蓮"},
    {"name": "Fitow", "wind": "33 (m/s)", "landfall": ""},
]

STEPS = [
    ("parse", "parse_numeric", {"column": "wind", "output": "wind_ms"}),
    ("flag", "flag_rows", {"column": "landfall", "op": "not_empty", "output": "landed"}),
    (
        "band",
        "bin_numeric",
        {
            "column": "wind_ms",
            "edges": [0, 40, 120],
            "labels": ["mild", "severe"],
            "output": "band",
        },
    ),
]


@pytest.fixture(scope="module")
def dataset_id(client, api) -> str:
    relative = "samples/test_scope_rows.csv"
    path = Path(get_settings().data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ROWS[0]))
        writer.writeheader()
        writer.writerows(ROWS)

    source = client.post(
        f"{api}/sources",
        json={"name": "scope rows", "type": "csv", "connection": {"path": relative}},
    )
    assert source.status_code == 201, source.text
    created = client.post(
        f"{api}/datasets", json={"name": "Scope rows", "source_id": source.json()["id"]}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


@pytest.fixture(scope="module")
def before(client, api, dataset_id) -> dict[str, set[str]]:
    """What the platform held before the pipeline ran.

    Depends on `dataset_id` so the input dataset is already in the snapshot:
    otherwise the fixture order decides whether it counts as something the
    pipeline created, and the test measures pytest rather than the platform.
    """
    return {
        "models": {m["id"] for m in client.get(f"{api}/models").json()},
        "datasets": {d["id"] for d in client.get(f"{api}/datasets?include=all").json()},
    }


@pytest.fixture(scope="module")
def pipeline(client, api, dataset_id, before) -> dict:
    """Three chained steps, none of which is a model in the library."""
    chained = []
    previous = None
    for name, transform, options in STEPS:
        chained.append(
            {
                "name": name,
                "provider": "python-transform",
                "configuration": {"transform": transform, "options": options},
                "input_from": previous,
            }
        )
        previous = name

    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Scope pipeline",
            "input_dataset_id": dataset_id,
            "steps": chained,
        },
    )
    assert created.status_code == 201, created.text
    run = client.post(f"{api}/pipelines/{created.json()['id']}/run", json={})
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "succeeded", run.json().get("error")
    return {"pipeline": created.json(), "run": run.json()}


# --------------------------------------------------------------------------
# what a run leaves behind
# --------------------------------------------------------------------------
def test_a_pipeline_adds_no_models_to_the_library(client, api, before, pipeline):
    """The whole point. Three steps used to mean three models."""
    after = {m["id"] for m in client.get(f"{api}/models").json()}
    assert after == before["models"], (
        f"the pipeline created models: {sorted(after - before['models'])}"
    )


def test_a_pipeline_adds_one_dataset_per_output_and_no_more(
    client, api, before, pipeline
):
    """Three steps, one deliverable. The two in the middle are checkpoints."""
    after = {d["id"] for d in client.get(f"{api}/datasets?include=all").json()}
    created = after - before["datasets"]
    assert len(created) == 1, f"expected one output dataset, got {len(created)}"
    assert set(pipeline["run"]["output_dataset_ids"]) == created


def test_intermediate_steps_produce_results_but_not_datasets(pipeline):
    """A checkpoint is still recorded; it just is not published."""
    by_name = {s["step_name"]: s for s in pipeline["run"]["step_runs"]}
    for name in ("parse", "flag"):
        assert by_name[name]["result_id"], f"{name} recorded nothing"
        assert not by_name[name]["dataset_version_id"], f"{name} published a dataset"
    assert by_name["band"]["dataset_version_id"]


def test_the_output_is_named_after_the_pipeline(client, api, pipeline):
    dataset_id = pipeline["run"]["output_dataset_ids"][0]
    dataset = client.get(f"{api}/datasets/{dataset_id}").json()
    #  Not "band result" - the final step's name is an implementation detail
    #  of the chain, and the reader is looking for the pipeline's output.
    assert dataset["name"] == "Scope pipeline output"
    assert "Scope pipeline" in dataset["description"]


def test_the_output_carries_every_step_s_work(client, api, pipeline):
    """Threading tables between steps must not lose anything."""
    version = pipeline["run"]["step_runs"][-1]["dataset_version_id"]
    preview = client.get(f"{api}/dataset-versions/{version}/preview?limit=10").json()
    columns = {c["name"] for c in preview["columns"]}
    #  wind_ms from step one, landed from step two, band from step three.
    assert {"name", "wind", "wind_ms", "landed", "band"} <= columns
    assert {row["band"] for row in preview["rows"]} == {"mild", "severe"}


# --------------------------------------------------------------------------
# the executions are still ordinary
# --------------------------------------------------------------------------
def test_a_step_is_an_ordinary_execution_with_its_definition_recorded(
    client, api, pipeline
):
    """An inline step runs the same path; what ran is on the execution."""
    step = pipeline["run"]["step_runs"][0]
    execution = client.get(f"{api}/executions/{step['execution_id']}")
    assert execution.status_code == 200, execution.text
    body = execution.json()

    assert body["status"] == "succeeded"
    #  No model row, because there is no model - but the definition that ran
    #  is recoverable from the record, which is the part that matters.
    assert body["model_id"] is None
    assert body["definition_snapshot"]["provider"] == "python-transform"
    assert body["definition_snapshot"]["configuration"]["transform"] == "parse_numeric"


def test_a_step_may_still_run_a_library_model_on_purpose(client, api, dataset_id):
    """Reuse is a choice, not the only option.

    A model that several pipelines share should be a model: improving it
    improves all of them. What changed is that this is now something you ask
    for rather than something that happens to every step.
    """
    model = client.post(
        f"{api}/models",
        json={
            "name": "Shared scope transform",
            "provider": "python-transform",
            "configuration": {
                "transform": "parse_numeric",
                "options": {"column": "wind", "output": "wind_ms"},
            },
        },
    )
    assert model.status_code == 201, model.text

    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Scope pipeline by reference",
            "input_dataset_id": dataset_id,
            "steps": [{"name": "parse", "model_id": model.json()["id"]}],
        },
    )
    assert created.status_code == 201, created.text
    run = client.post(f"{api}/pipelines/{created.json()['id']}/run", json={})
    assert run.json()["status"] == "succeeded", run.json().get("error")

    execution_id = run.json()["step_runs"][0]["execution_id"]
    body = client.get(f"{api}/executions/{execution_id}").json()
    assert body["model_id"] == model.json()["id"]


def test_a_step_that_names_neither_a_model_nor_a_provider_is_refused(
    client, api, dataset_id
):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Scope pipeline with nothing to run",
            "input_dataset_id": dataset_id,
            "steps": [{"name": "empty"}],
        },
    )
    assert response.status_code == 422, response.text
    #  A step now has three ways to say what it runs; naming none of them is
    #  still the one thing it may not do.
    assert "must name a provider, a model_id or a pipeline_id" in response.text


def test_an_inline_step_with_a_broken_configuration_is_refused_at_save_time(
    client, api, dataset_id
):
    """The error arrives while you are editing, which is when it is useful."""
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Scope pipeline with a bad step",
            "input_dataset_id": dataset_id,
            "steps": [
                {
                    "name": "broken",
                    "provider": "python-transform",
                    "configuration": {"transform": "no_such_transform"},
                }
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert "no_such_transform" in response.text


# --------------------------------------------------------------------------
# the catalogue stays readable
# --------------------------------------------------------------------------
def test_an_ingested_dataset_is_never_demoted(client, api, dataset_id, pipeline):
    """What a person ingested stays where they put it."""
    dataset = client.get(f"{api}/datasets/{dataset_id}").json()
    assert dataset["origin"] == "source"


def test_datasets_are_searchable(client, api, dataset_id):
    found = client.get(f"{api}/datasets?search=Scope").json()
    assert dataset_id in {d["id"] for d in found}


def test_the_library_is_searchable(client, api):
    created = client.post(
        f"{api}/models",
        json={
            "name": "Scope searchable model",
            "provider": "formula",
            "configuration": {"expressions": {"x": "1"}},
        },
    )
    assert created.status_code == 201, created.text
    found = client.get(f"{api}/models?search=Scope searchable").json()
    assert {m["name"] for m in found} == {"Scope searchable model"}
