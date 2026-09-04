"""Training is an Execution that produces a Model Version.

    Dataset -> Training Execution -> Model v2 -> Prediction Execution -> Result
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

import pytest

from app.core.config import get_settings


@pytest.fixture(scope="module")
def training_dataset(client, api) -> dict:
    relative = "Demo/sources/test_training.csv"
    path = Path(get_settings().data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["x", "y"])
        for _ in range(120):
            x = rng.uniform(0, 100)
            writer.writerow([round(x, 4), round(3 * x + 5 + rng.gauss(0, 2), 4)])

    source = client.post(
        f"{api}/sources",
        json={"name": "training sample", "type": "csv", "connection": {"path": relative}},
    ).json()
    return client.post(
        f"{api}/datasets",
        json={"name": "Training sample", "source_id": source["id"]},
    ).json()


@pytest.fixture(scope="module")
def trainable_model(client, api) -> dict:
    response = client.post(
        f"{api}/models",
        json={
            "name": "Linear y from x",
            "provider": "sklearn",
            "configuration": {
                "algorithm": "linear_regression",
                "target": "y",
                "features": ["x"],
                "test_size": 0.25,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_training_execution_publishes_an_immutable_version(
    client, api, trainable_model, training_dataset
):
    versions_before = client.get(
        f"{api}/models/{trainable_model['id']}/versions"
    ).json()
    assert len(versions_before) == 1  # every model starts at v1

    run = client.post(
        f"{api}/executions",
        json={
            "model_id": trainable_model["id"],
            "kind": "training",
            "dataset_id": training_dataset["id"],
        },
    )
    assert run.status_code == 201, run.text
    execution = run.json()
    assert execution["status"] == "succeeded"
    assert execution["produced_model_version_id"]
    assert execution["metrics"]["r2"] > 0.95

    versions_after = client.get(f"{api}/models/{trainable_model['id']}/versions").json()
    assert len(versions_after) == 2
    newest = versions_after[0]
    assert newest["version"] == 2
    assert newest["artifact_uri"]
    assert newest["created_by_execution_id"] == execution["id"]
    #  v1 is untouched: versions are never rewritten.
    assert versions_after[1]["version"] == 1
    assert versions_after[1]["artifact_uri"] is None


def test_prediction_uses_the_trained_version(
    client, api, trainable_model, training_dataset
):
    run = client.post(
        f"{api}/executions",
        json={
            "model_id": trainable_model["id"],
            "kind": "prediction",
            "input": {"rows": [{"x": 10.0}, {"x": 50.0}]},
        },
    )
    assert run.status_code == 201, run.text
    execution = run.json()
    assert execution["status"] == "succeeded"

    payload = client.get(
        f"{api}/results/{execution['result_id']}/payload"
    ).json()["payload"]
    predictions = [row["prediction"] for row in payload["rows"]]
    assert predictions[0] == pytest.approx(35, abs=3)
    assert predictions[1] == pytest.approx(155, abs=3)


def test_prediction_before_training_is_refused(client, api):
    untrained = client.post(
        f"{api}/models",
        json={
            "name": "Never trained",
            "provider": "sklearn",
            "configuration": {
                "algorithm": "linear_regression",
                "target": "y",
                "features": ["x"],
            },
        },
    ).json()
    response = client.post(
        f"{api}/executions",
        json={
            "model_id": untrained["id"],
            "kind": "prediction",
            "input": {"rows": [{"x": 1.0}]},
        },
    )
    assert response.status_code == 400
    assert "training execution" in response.json()["message"]
