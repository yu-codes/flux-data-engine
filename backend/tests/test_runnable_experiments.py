"""An experiment you can check, run, and compare.

    Experiment (trials + dataset) -> check -> run -> compare

The point of the check is that a misconfigured comparison says so *before* it
consumes an execution slot, and says which trial and why - an experiment that
fails halfway through leaves you with a leaderboard you cannot trust.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

READINGS = [
    {"wind_ms": 18.0, "min_pressure": 990.0, "label": "TS"},
    {"wind_ms": 33.0, "min_pressure": 965.0, "label": "TY"},
    {"wind_ms": 45.0, "min_pressure": 940.0, "label": "TY"},
    {"wind_ms": 52.0, "min_pressure": 920.0, "label": "STY"},
    {"wind_ms": 24.0, "min_pressure": 980.0, "label": "TS"},
]


def _write_csv(relative: str, rows: list[dict]) -> str:
    settings = get_settings()
    path = Path(settings.data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return relative


def _dataset_from(client, api, name: str, relative: str) -> dict:
    source = client.post(
        f"{api}/sources",
        json={"name": name.lower(), "type": "csv", "connection": {"path": relative}},
    )
    assert source.status_code == 201, source.text
    created = client.post(
        f"{api}/datasets",
        json={"name": name, "source_id": source.json()["id"]},
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture(scope="module")
def dataset(client, api) -> dict:
    relative = _write_csv("samples/test_readings.csv", READINGS)
    return _dataset_from(client, api, "Experiment readings", relative)


@pytest.fixture(scope="module")
def models(client, api) -> dict:
    """Two providers that measure different things, on purpose."""
    made = {}
    formula = client.post(
        f"{api}/models",
        json={
            "name": "Runnable pressure drop",
            "provider": "formula",
            "configuration": {"expressions": {"drop": "1013 - min_pressure"}},
        },
    )
    assert formula.status_code == 201, formula.text
    made["formula"] = formula.json()

    curve = client.post(
        f"{api}/models",
        json={
            "name": "Runnable wind-pressure fit",
            "provider": "curve-fit",
            "configuration": {"x": "wind_ms", "y": "min_pressure", "family": "linear"},
        },
    )
    assert curve.status_code == 201, curve.text
    made["curve"] = curve.json()
    return made


def _create(client, api, **body) -> dict:
    created = client.post(f"{api}/experiments", json=body)
    assert created.status_code == 201, created.text
    return created.json()


# --------------------------------------------------------------------------
# specification
# --------------------------------------------------------------------------
def test_an_experiment_holds_trials_and_one_dataset(client, api, dataset, models):
    experiment = _create(
        client, api,
        name="Trials are the unit",
        primary_metric="rows_processed",
        dataset_version_id=dataset["current_version_id"],
        trials=[
            {"model_id": models["formula"]["id"], "label": "as configured"},
            {"model_id": models["curve"]["id"], "label": "linear fit"},
        ],
    )

    assert experiment["dataset_version_id"] == dataset["current_version_id"]
    assert [t["label"] for t in experiment["trials"]] == ["as configured", "linear fit"]
    #  One dataset for the whole experiment: trials are only comparable if they
    #  were measured on the same thing.
    assert "dataset_version_id" not in experiment["trials"][0]


def test_the_same_model_can_appear_twice_with_different_parameters(
    client, api, dataset, models
):
    experiment = _create(
        client, api,
        name="One model, two settings",
        dataset_version_id=dataset["current_version_id"],
        trials=[
            {"model_id": models["curve"]["id"], "label": "linear", "parameters": {}},
            {"model_id": models["curve"]["id"], "label": "quadratic", "parameters": {}},
        ],
    )
    assert len(experiment["trials"]) == 2
    #  Two trials, one distinct model - which is what makes a sweep expressible.
    assert experiment["model_ids"] == [models["curve"]["id"]]


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------
def test_a_well_formed_experiment_checks_out_runnable(client, api, dataset, models):
    experiment = _create(
        client, api,
        name="Runnable comparison",
        primary_metric="rows_processed",
        dataset_version_id=dataset["current_version_id"],
        trials=[
            {"model_id": models["formula"]["id"], "label": "pressure drop"},
            {"model_id": models["curve"]["id"], "label": "wind fit"},
        ],
    )

    report = client.get(f"{api}/experiments/{experiment['id']}/check")
    assert report.status_code == 200, report.text
    body = report.json()

    assert body["runnable"] is True, body
    assert body["errors"] == []
    assert len(body["trials"]) == 2
    assert all(trial["runnable"] for trial in body["trials"])


def test_the_check_names_the_columns_a_provider_cannot_find(client, api, models):
    """curve-fit reads columns named in its own configuration, so it can say so."""
    relative = _write_csv(
        "samples/test_unrelated.csv", [{"city": "Taipei", "population": 2600000}]
    )
    other = _dataset_from(client, api, "Unrelated cities", relative)

    experiment = _create(
        client, api,
        name="Wrong dataset for the fit",
        dataset_version_id=other["current_version_id"],
        trials=[{"model_id": models["curve"]["id"], "label": "fit on the wrong table"}],
    )

    body = client.get(f"{api}/experiments/{experiment['id']}/check").json()
    assert body["runnable"] is False
    errors = " ".join(body["trials"][0]["errors"])
    #  The complaint names the column and which role it was needed for.
    assert "wind_ms" in errors
    assert "min_pressure" in errors


def test_a_deleted_model_blocks_the_experiment_that_names_it(client, api, dataset):
    doomed = client.post(
        f"{api}/models",
        json={
            "name": "Model that will not last",
            "provider": "formula",
            "configuration": {"expressions": {"x": "1"}},
        },
    )
    assert doomed.status_code == 201, doomed.text
    model_id = doomed.json()["id"]

    experiment = _create(
        client, api,
        name="Comparison against a ghost",
        dataset_version_id=dataset["current_version_id"],
        trials=[{"model_id": model_id, "label": "ghost"}],
    )
    assert client.delete(f"{api}/models/{model_id}").status_code in (200, 204)

    body = client.get(f"{api}/experiments/{experiment['id']}/check").json()
    assert body["runnable"] is False
    assert any("no longer exists" in e for e in body["trials"][0]["errors"])


def test_an_unrunnable_experiment_refuses_to_run(client, api, dataset):
    experiment = _create(
        client, api,
        name="Refuses to run",
        dataset_version_id=dataset["current_version_id"],
        trials=[{"model_id": "mdl_does_not_exist", "label": "nothing"}],
    )
    response = client.post(f"{api}/experiments/{experiment['id']}/run")
    #  Refused before anything is submitted, not halfway through.
    assert response.status_code == 422, response.text
    assert client.get(f"{api}/experiments/{experiment['id']}").json()["execution_ids"] == []


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def run_experiment(client, api, dataset, models) -> dict:
    experiment = _create(
        client, api,
        name="Executed as one unit",
        primary_metric="rows_processed",
        dataset_version_id=dataset["current_version_id"],
        trials=[
            {"model_id": models["formula"]["id"], "label": "pressure drop"},
            {"model_id": models["curve"]["id"], "label": "wind fit"},
        ],
    )
    response = client.post(f"{api}/experiments/{experiment['id']}/run")
    assert response.status_code in (200, 201), response.text
    return {"experiment": experiment, "run": response.json()}


def test_running_an_experiment_submits_every_trial(client, api, run_experiment):
    experiment_id = run_experiment["experiment"]["id"]
    detail = client.get(f"{api}/experiments/{experiment_id}").json()

    #  One action, one execution per trial. That is what "the unit of execution
    #  is the experiment" has to mean in practice.
    assert len(detail["execution_ids"]) == 2

    for execution_id in detail["execution_ids"]:
        execution = client.get(f"{api}/executions/{execution_id}").json()
        assert execution["experiment_id"] == experiment_id
        assert execution["status"] == "succeeded", execution


# --------------------------------------------------------------------------
# compare
# --------------------------------------------------------------------------
def test_comparison_discovers_metric_names_from_the_runs(client, api, run_experiment):
    experiment_id = run_experiment["experiment"]["id"]
    response = client.post(
        f"{api}/experiments/compare", json={"experiment_ids": [experiment_id]}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["rows"]) == 2
    #  Nothing here was named in advance: two providers reporting different
    #  metrics both get columns, which is the whole point of not hardcoding them.
    assert "rows_processed" in body["metric_names"]
    assert len(body["metric_names"]) > 1
    labels = {row["trial"] for row in body["rows"]}
    assert labels == {"pressure drop", "wind fit"}


def test_comparison_spans_experiments_and_ranks_on_a_shared_metric(
    client, api, dataset, models, run_experiment
):
    second = _create(
        client, api,
        name="A second opinion",
        primary_metric="rows_processed",
        dataset_version_id=dataset["current_version_id"],
        trials=[{"model_id": models["formula"]["id"], "label": "same formula again"}],
    )
    assert client.post(f"{api}/experiments/{second['id']}/run").status_code in (200, 201)

    ids = [run_experiment["experiment"]["id"], second["id"]]
    body = client.post(
        f"{api}/experiments/compare",
        json={"experiment_ids": ids, "metric": "rows_processed"},
    ).json()

    assert len(body["experiments"]) == 2
    assert len(body["rows"]) == 3
    assert body["ranked_by"] == "rows_processed"
    #  Ranked, so the table has an order to read down. A trial that never
    #  reported the ranking metric sinks to the bottom rather than being
    #  dropped - it still ran, and hiding it would misrepresent the comparison.
    values = [row["metrics"].get("rows_processed") for row in body["rows"]]
    scored = [v for v in values if v is not None]
    assert scored == sorted(scored, reverse=True)
    assert values[: len(scored)] == scored
    assert {row["experiment"] for row in body["rows"]} == {
        "Executed as one unit",
        "A second opinion",
    }


def test_rerunning_does_not_duplicate_rows(client, api, run_experiment):
    """A comparison shows where each trial stands, not how often it was run."""
    experiment_id = run_experiment["experiment"]["id"]
    assert client.post(f"{api}/experiments/{experiment_id}/run").status_code in (200, 201)

    body = client.post(
        f"{api}/experiments/compare", json={"experiment_ids": [experiment_id]}
    ).json()
    assert len(body["rows"]) == 2

    #  The earlier runs are still there and can be asked for by name.
    history = client.post(
        f"{api}/experiments/compare",
        json={"experiment_ids": [experiment_id], "include_history": True},
    ).json()
    assert len(history["rows"]) > 2


def test_two_trials_of_one_model_stay_distinct(client, api, dataset, models):
    """Matching runs on model_id alone would fold these two into one row."""
    experiment = _create(
        client, api,
        name="One model, two labelled trials",
        primary_metric="rows_processed",
        dataset_version_id=dataset["current_version_id"],
        trials=[
            {"model_id": models["formula"]["id"], "label": "first pass"},
            {"model_id": models["formula"]["id"], "label": "second pass"},
        ],
    )
    assert client.post(
        f"{api}/experiments/{experiment['id']}/run"
    ).status_code in (200, 201)

    body = client.post(
        f"{api}/experiments/compare", json={"experiment_ids": [experiment["id"]]}
    ).json()
    assert {row["trial"] for row in body["rows"]} == {"first pass", "second pass"}


def test_the_leaderboard_shows_a_row_per_trial_without_an_evaluation(
    client, api, dataset, models
):
    """A sweep is one model at several settings, and nobody scores it by hand."""
    experiment = _create(
        client, api,
        name="Leaderboard from runs alone",
        primary_metric="rows_processed",
        dataset_version_id=dataset["current_version_id"],
        trials=[
            {"model_id": models["formula"]["id"], "label": "setting A"},
            {"model_id": models["formula"]["id"], "label": "setting B"},
        ],
    )
    assert client.post(
        f"{api}/experiments/{experiment['id']}/run"
    ).status_code in (200, 201)

    board = client.get(f"{api}/experiments/{experiment['id']}/leaderboard")
    assert board.status_code == 200, board.text
    body = board.json()

    #  Two trials of one model are two rows: keying on the model collapsed them.
    assert [row["trial"] for row in body["rows"]] == ["setting A", "setting B"]
    #  No evaluation was recorded, so the measurement stands in for the
    #  judgement rather than the row reading "not evaluated".
    assert all(row["primary_value"] is not None for row in body["rows"])
    assert all(row["passed"] is None for row in body["rows"])
    assert all(row["execution_id"] for row in body["rows"])
    assert "rows_processed" in body["metric_names"]
