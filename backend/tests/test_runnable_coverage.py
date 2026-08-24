"""A Pipeline is a runnable, and everything built on execution knows it.

A Model and a Pipeline both fit the platform's own formula - inputs,
parameters, a versioned definition, an output - and for a long time only one
of them could be executed. Everything built on top of execution therefore had
to be built twice or denied to the second: a pipeline could not be scheduled,
compared in an experiment, served, or nested inside another pipeline, and each
of those was a separate absence with a separate excuse.

This file is the rule that keeps that closed. Each capability is asserted for
every runnable kind, so adding a third kind - an ensemble, an agent flow -
fails here until it can do what the others can, rather than quietly arriving
able to do one thing.
"""

from __future__ import annotations

import pytest

from app.modules.execution.domain.entities import RunnableKind


@pytest.fixture(scope="module")
def dataset(client, api) -> dict:
    source = client.post(
        f"{api}/sources",
        json={
            "name": "runnable rows",
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
        json={"name": "Runnable data", "source_id": source.json()["id"]},
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture(scope="module")
def model(client, api) -> dict:
    created = client.post(
        f"{api}/models",
        json={
            "name": "Runnable model",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "units * 2"}},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


@pytest.fixture(scope="module")
def pipeline(client, api, dataset) -> dict:
    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Runnable pipeline",
            "input_dataset_id": dataset["id"],
            "steps": [
                {
                    "name": "keep the busy ones",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "filter_rows",
                        "options": {"column": "units", "op": "gt", "value": 2},
                    },
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


# --------------------------------------------------------------------------
# every kind can be executed
# --------------------------------------------------------------------------
def test_every_runnable_kind_can_be_executed(client, api, model, pipeline, dataset):
    """The rule, stated once for every kind there is."""
    targets = {
        RunnableKind.MODEL.value: model["id"],
        RunnableKind.PIPELINE.value: pipeline["id"],
    }
    assert set(targets) == {kind.value for kind in RunnableKind}, (
        "a runnable kind exists that this file does not exercise"
    )

    for kind, target_id in targets.items():
        response = client.post(
            f"{api}/executions",
            json={
                "target_id": target_id,
                "target_type": kind,
                "dataset_id": dataset["id"],
            },
        )
        assert response.status_code == 201, f"{kind}: {response.text}"
        body = response.json()
        assert body["status"] == "succeeded", f"{kind}: {body.get('error')}"
        assert body["target_type"] == kind
        assert body["target_id"] == target_id
        assert body["result_id"], f"{kind} produced no result"


def test_an_execution_says_which_model_only_when_it_ran_one(
    client, api, model, pipeline, dataset
):
    """`model_id` answers "which model", not "what ran".

    Answering with a pipeline's id would make every caller that filters by
    model quietly wrong, which is worse than answering nothing.
    """
    ran_model = client.post(
        f"{api}/executions",
        json={"model_id": model["id"], "dataset_id": dataset["id"]},
    ).json()
    ran_pipeline = client.post(
        f"{api}/executions",
        json={
            "target_id": pipeline["id"],
            "target_type": "pipeline",
            "dataset_id": dataset["id"],
        },
    ).json()

    assert ran_model["model_id"] == model["id"]
    assert ran_pipeline["model_id"] is None
    assert ran_pipeline["target_id"] == pipeline["id"]


def test_a_pipeline_execution_answers_with_the_rows_it_produced(
    client, api, pipeline, dataset
):
    """Serving a pipeline has to return its output, not a report about itself."""
    execution = client.post(
        f"{api}/executions",
        json={
            "target_id": pipeline["id"],
            "target_type": "pipeline",
            "dataset_id": dataset["id"],
        },
    ).json()

    payload = client.get(f"{api}/results/{execution['result_id']}/payload")
    assert payload.status_code == 200, payload.text
    rows = payload.json()["payload"]["rows"]

    #  The step keeps units > 2, so Taichung is gone.
    assert {row["city"] for row in rows} == {"Taipei", "Tainan"}


def test_a_pipeline_run_and_its_execution_can_find_each_other(
    client, api, pipeline, dataset
):
    execution = client.post(
        f"{api}/executions",
        json={
            "target_id": pipeline["id"],
            "target_type": "pipeline",
            "dataset_id": dataset["id"],
        },
    ).json()

    run_id = client.get(f"{api}/results/{execution['result_id']}").json()["summary"][
        "pipeline_run_id"
    ]
    run = client.get(f"{api}/pipeline-runs/{run_id}")
    assert run.status_code == 200, run.text
    assert run.json()["execution_id"] == execution["id"]


def test_executions_can_be_listed_by_what_they_ran(client, api, pipeline, dataset):
    client.post(
        f"{api}/executions",
        json={
            "target_id": pipeline["id"],
            "target_type": "pipeline",
            "dataset_id": dataset["id"],
        },
    )
    listed = client.get(f"{api}/executions", params={"target_type": "pipeline"})
    assert listed.status_code == 200, listed.text
    assert listed.json(), "no pipeline executions listed"
    assert all(e["target_type"] == "pipeline" for e in listed.json())


def test_a_failing_step_makes_the_execution_fail(client, api, dataset):
    """A pipeline that breaks must not report success through the new path."""
    broken = client.post(
        f"{api}/pipelines",
        json={
            "name": "Runnable pipeline that breaks",
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
    assert broken.status_code == 201, broken.text

    response = client.post(
        f"{api}/executions",
        json={
            "target_id": broken.json()["id"],
            "target_type": "pipeline",
            "dataset_id": dataset["id"],
        },
    )
    #  However it is reported, it must not be reported as having worked.
    if response.status_code == 201:
        assert response.json()["status"] == "failed"
    else:
        assert response.status_code >= 400, response.text


# --------------------------------------------------------------------------
# every kind can be compared
# --------------------------------------------------------------------------
def test_two_pipelines_can_be_compared_in_one_experiment(client, api, dataset):
    """"Which of these two ways of preparing the data is better" was unaskable.

    It is the same question as "which of these two models is better", and the
    platform could only ask one of them - not because comparing pipelines is
    hard, but because a trial could only name a model.
    """
    pipelines = []
    for name, threshold in (("strict", 5), ("loose", 1)):
        created = client.post(
            f"{api}/pipelines",
            json={
                "name": f"Compared {name}",
                "input_dataset_id": dataset["id"],
                "steps": [
                    {
                        "name": "keep some",
                        "provider": "python-transform",
                        "configuration": {
                            "transform": "filter_rows",
                            "options": {
                                "column": "units", "op": "gt", "value": threshold,
                            },
                        },
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        pipelines.append((name, created.json()["id"]))

    experiment = client.post(
        f"{api}/experiments",
        json={
            "name": "Which way of preparing the data",
            "primary_metric": "succeeded_steps",
            "dataset_version_id": dataset["current_version_id"],
            "trials": [
                {"target_id": pid, "target_type": "pipeline", "label": name}
                for name, pid in pipelines
            ],
        },
    )
    assert experiment.status_code == 201, experiment.text
    experiment_id = experiment.json()["id"]

    #  It must say it can run before it runs: a comparison that only discovers
    #  it is broken half way through has already wasted the run.
    check = client.get(f"{api}/experiments/{experiment_id}/check")
    assert check.status_code == 200, check.text
    assert check.json()["runnable"], check.json()

    run = client.post(f"{api}/experiments/{experiment_id}/run")
    assert run.status_code == 200, run.text

    board = client.get(f"{api}/experiments/{experiment_id}/leaderboard")
    assert board.status_code == 200, board.text
    rows = board.json()["rows"]
    assert {row["trial"] for row in rows} == {"strict", "loose"}


def test_a_trial_naming_a_pipeline_that_is_gone_is_refused_before_running(
    client, api, dataset
):
    """The check has to cover every kind of trial, not only models."""
    experiment = client.post(
        f"{api}/experiments",
        json={
            "name": "Compares a ghost",
            "dataset_version_id": dataset["current_version_id"],
            "trials": [{"target_id": "pipe_nope", "target_type": "pipeline"}],
        },
    )
    assert experiment.status_code == 201, experiment.text

    check = client.get(f"{api}/experiments/{experiment.json()['id']}/check")
    assert check.status_code == 200, check.text
    body = check.json()
    assert not body["runnable"]
    assert "no longer exists" in str(body)


# --------------------------------------------------------------------------
# every kind can be called
# --------------------------------------------------------------------------
def test_a_pipeline_can_be_invoked_like_a_model(client, api, model, pipeline, dataset):
    """One response shape, whatever kind of runnable answered.

    An integration that can read a model's answer can read a pipeline's, which
    is the only thing that makes "both are runnables" mean anything to the
    caller rather than only to the code.
    """
    called_model = client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"dataset_id": dataset["id"]},
    )
    called_pipeline = client.post(
        f"{api}/pipelines/{pipeline['id']}/invoke",
        json={"dataset_id": dataset["id"]},
    )
    assert called_model.status_code == 200, called_model.text
    assert called_pipeline.status_code == 200, called_pipeline.text
    assert set(called_model.json()) == set(called_pipeline.json())

    answered = called_pipeline.json()
    assert answered["target_type"] == "pipeline"
    assert answered["target_id"] == pipeline["id"]
    assert answered["model_id"] is None
    assert {row["city"] for row in answered["rows"]} == {"Taipei", "Tainan"}


def test_invoking_a_pipeline_applies_it_to_the_rows_the_caller_brings(
    client, api, pipeline
):
    """The difference between a batch job and a callable transformation."""
    answered = client.post(
        f"{api}/pipelines/{pipeline['id']}/invoke",
        json={
            "rows": [
                {"city": "Kaohsiung", "units": 8},
                {"city": "Hualien", "units": 1},
            ]
        },
    )
    assert answered.status_code == 200, answered.text
    assert {row["city"] for row in answered.json()["rows"]} == {"Kaohsiung"}


def test_invoking_a_pipeline_records_nothing(client, api, pipeline, dataset):
    """"Nothing is recorded" is the contract; a run row would break it."""
    before = client.get(f"{api}/pipeline-runs", params={"pipeline_id": pipeline["id"]})
    assert before.status_code == 200, before.text
    counted = len(before.json())

    client.post(
        f"{api}/pipelines/{pipeline['id']}/invoke", json={"dataset_id": dataset["id"]}
    )

    after = client.get(f"{api}/pipeline-runs", params={"pipeline_id": pipeline["id"]})
    assert len(after.json()) == counted


def test_invoking_a_pipeline_that_breaks_says_which_step(client, api, dataset):
    """A synchronous caller has no run record to go and read afterwards."""
    broken = client.post(
        f"{api}/pipelines",
        json={
            "name": "Invoked pipeline that breaks",
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
    assert broken.status_code == 201, broken.text

    answered = client.post(
        f"{api}/pipelines/{broken.json()['id']}/invoke",
        json={"dataset_id": dataset["id"]},
    )
    assert answered.status_code >= 400, answered.text
    assert "filter a column that is not there" in answered.text


def test_a_queued_pipeline_execution_is_run_by_the_worker(client, api, pipeline, dataset):
    """The worker path, which is the one a real deployment uses.

    In queue mode `submit` records the execution and hands the id off; what
    runs it later is `run()`, in the worker's own session with its own service
    graph. A runner wired only into the request path would leave every queued
    pipeline execution pending for ever.
    """
    from app.core.container import build_services
    from app.core.database import session_scope

    class NeverRuns:
        runs_inline = False
        mode = "queue"

        def enqueue(self, execution_id: str) -> None:
            """A worker that is not there yet."""

    with session_scope() as session:
        services = build_services(session)
        services.executions.dispatcher = NeverRuns()
        submitted = services.executions.submit(
            pipeline_id=pipeline["id"], dataset_id=dataset["id"]
        )
        assert submitted.status.value == "pending", submitted.status

    #  A different session, as the worker would have.
    with session_scope() as session:
        finished = build_services(session).executions.run(submitted.id)

    assert finished.status.value == "succeeded", finished.error
    assert finished.result_id
