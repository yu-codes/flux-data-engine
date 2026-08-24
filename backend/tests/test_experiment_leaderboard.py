"""An Experiment is only useful if it can answer 'which one won?'.

The leaderboard joins the experiment's models to their newest evaluation and
ranks them on the experiment's primary metric.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def experiment(client, api) -> dict:
    models = []
    for name, expression in [
        ("Leaderboard model A", "value * 1"),
        ("Leaderboard model B", "value * 2"),
        ("Leaderboard model C", "value * 3"),
    ]:
        created = client.post(
            f"{api}/models",
            json={
                "name": name,
                "provider": "formula",
                "configuration": {"expressions": {"scaled": expression}},
            },
        )
        assert created.status_code == 201, created.text
        models.append(created.json())

    created = client.post(
        f"{api}/experiments",
        json={
            "name": "Leaderboard comparison",
            "objective": "maximise accuracy",
            "primary_metric": "accuracy",
            "model_ids": [m["id"] for m in models],
        },
    )
    assert created.status_code == 201, created.text
    return {"experiment": created.json(), "models": models}


def test_models_without_an_evaluation_rank_last(client, api, experiment):
    board = client.get(f"{api}/experiments/{experiment['experiment']['id']}/leaderboard")
    assert board.status_code == 200, board.text
    body = board.json()

    assert body["primary_metric"] == "accuracy"
    assert len(body["rows"]) == 3
    #  Nothing has been evaluated yet, so every row is unranked but present.
    assert all(row["primary_value"] is None for row in body["rows"])
    assert all(row["passed"] is None for row in body["rows"])
    assert body["metric_names"] == []


def test_leaderboard_ranks_on_the_primary_metric(client, api, experiment):
    experiment_id = experiment["experiment"]["id"]
    scores = {0: 0.42, 1: 0.91, 2: 0.66}

    for index, accuracy in scores.items():
        model = experiment["models"][index]
        execution = client.post(
            f"{api}/executions",
            json={
                "model_id": model["id"],
                "kind": "calculation",
                "experiment_id": experiment_id,
                "input": {"value": 1},
            },
        )
        assert execution.status_code == 201, execution.text
        recorded = client.post(
            f"{api}/evaluations",
            json={
                "execution_id": execution.json()["id"],
                "model_id": model["id"],
                "experiment_id": experiment_id,
                "metrics": {"accuracy": accuracy, "sample_size": 100},
                "target": {"metric": "accuracy", "min": 0.5},
            },
        )
        assert recorded.status_code == 201, recorded.text

    body = client.get(f"{api}/experiments/{experiment_id}/leaderboard").json()

    #  Best first, and the metric columns are discovered from the evaluations.
    assert [row["primary_value"] for row in body["rows"]] == [0.91, 0.66, 0.42]
    assert body["rows"][0]["model_name"] == "Leaderboard model B"
    assert body["metric_names"] == ["accuracy", "sample_size"]

    #  The target travels with the evaluation, so the board can say who met it.
    assert [row["passed"] for row in body["rows"]] == [True, True, False]
    assert all(row["execution_id"] for row in body["rows"])


def test_only_the_newest_evaluation_per_model_is_shown(client, api, experiment):
    experiment_id = experiment["experiment"]["id"]
    model = experiment["models"][0]

    execution = client.post(
        f"{api}/executions",
        json={
            "model_id": model["id"],
            "kind": "calculation",
            "experiment_id": experiment_id,
            "input": {"value": 1},
        },
    )
    assert execution.status_code == 201, execution.text
    client.post(
        f"{api}/evaluations",
        json={
            "execution_id": execution.json()["id"],
            "model_id": model["id"],
            "experiment_id": experiment_id,
            "metrics": {"accuracy": 0.99, "sample_size": 100},
            "target": {"metric": "accuracy", "min": 0.5},
        },
    )

    body = client.get(f"{api}/experiments/{experiment_id}/leaderboard").json()
    rows = {row["model_id"]: row for row in body["rows"]}
    assert rows[model["id"]]["primary_value"] == 0.99
    assert body["rows"][0]["model_id"] == model["id"]


# --------------------------------------------------------------------------
# which way is better
# --------------------------------------------------------------------------
def test_an_experiment_ranked_by_error_puts_the_smallest_first(client, api):
    """Ranking assumed higher was better, for every metric.

    An experiment whose primary metric is RMSE therefore put the worst trial at
    the top and labelled it the leader - a wrong answer presented with the same
    confidence as a right one. Direction is a property of the comparison, so
    the experiment declares it.
    """
    experiment = client.post(
        f"{api}/experiments",
        json={
            "name": "Lowest error wins",
            "primary_metric": "rmse",
            "primary_direction": "lower",
            "trials": [],
        },
    )
    assert experiment.status_code == 201, experiment.text
    assert experiment.json()["primary_direction"] == "lower"

    for name, rmse in (("worse", 9.0), ("better", 1.0)):
        created = client.post(
            f"{api}/models",
            json={
                "name": f"Error {name}",
                "provider": "formula",
                "configuration": {"expressions": {"x": "price * 1"}},
            },
        )
        assert created.status_code == 201, created.text
        execution = client.post(
            f"{api}/executions",
            json={
                "model_id": created.json()["id"],
                "input": {"rows": [{"price": 1}]},
            },
        )
        assert execution.status_code == 201, execution.text
        recorded = client.post(
            f"{api}/evaluations",
            json={
                "execution_id": execution.json()["id"],
                "model_id": created.json()["id"],
                "experiment_id": experiment.json()["id"],
                "metrics": {"rmse": rmse},
            },
        )
        assert recorded.status_code == 201, recorded.text

    board = client.get(f"{api}/experiments/{experiment.json()['id']}/leaderboard")
    assert board.status_code == 200, board.text
    body = board.json()

    assert body["primary_direction"] == "lower"
    ranked = [row["primary_value"] for row in body["rows"] if row["primary_value"]]
    assert ranked == sorted(ranked), f"smallest error should lead, got {ranked}"


def test_a_direction_that_is_neither_is_refused(client, api):
    response = client.post(
        f"{api}/experiments",
        json={"name": "Sideways", "primary_metric": "rmse",
              "primary_direction": "sideways", "trials": []},
    )
    assert response.status_code == 422, response.text
    assert "higher" in response.text
