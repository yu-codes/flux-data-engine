"""The golden path, end to end.

    CSV source -> Dataset -> Schema -> Formula model -> Execution -> Result
                                                                 -> Visualization
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

SALES_ROWS = [
    {"date": "2026-01-01", "product": "Widget", "price": 25.0, "quantity": 4},
    {"date": "2026-01-01", "product": "Gadget", "price": 60.0, "quantity": 2},
    {"date": "2026-01-02", "product": "Widget", "price": 25.0, "quantity": 10},
    {"date": "2026-01-02", "product": "Gadget", "price": 62.5, "quantity": 31},
    {"date": "2026-01-03", "product": "Widget", "price": 24.0, "quantity": 7},
    {"date": "2026-01-03", "product": "Gadget", "price": 59.0, "quantity": 12},
]


@pytest.fixture(scope="module")
def sales_csv() -> str:
    settings = get_settings()
    relative = "Demo/sources/test_sales.csv"
    path = Path(settings.data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SALES_ROWS[0]))
        writer.writeheader()
        writer.writerows(SALES_ROWS)
    return relative


@pytest.fixture(scope="module")
def dataset(client, api, sales_csv) -> dict:
    source = client.post(
        f"{api}/sources",
        json={
            "name": "golden path sales",
            "type": "csv",
            "connection": {"path": sales_csv},
        },
    )
    assert source.status_code == 201, source.text
    created = client.post(
        f"{api}/datasets",
        json={"name": "Golden path sales", "source_id": source.json()["id"]},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_dataset_gets_an_inferred_schema(dataset):
    names = {field["name"] for field in dataset["schema_fields"]}
    assert names == {"date", "product", "price", "quantity"}
    types = {f["name"]: f["type"] for f in dataset["schema_fields"]}
    assert types["price"] == "float"
    assert types["quantity"] == "integer"
    assert dataset["versions"][0]["row_count"] == len(SALES_ROWS)


def test_formula_model_execution_produces_a_result_dataset(client, api, dataset):
    model = client.post(
        f"{api}/models",
        json={
            "name": "Golden path revenue",
            "provider": "formula",
            "configuration": {"expressions": {"revenue": "price * quantity"}},
        },
    )
    assert model.status_code == 201, model.text
    body = model.json()
    #  A formula model is a Model without being trainable, and it starts at v1.
    assert body["type"] == "formula"
    assert body["trainable"] is False
    assert body["current_version_id"]

    execution = client.post(
        f"{api}/executions",
        json={
            "model_id": body["id"],
            "kind": "calculation",
            "dataset_id": dataset["id"],
        },
    )
    assert execution.status_code == 201, execution.text
    run = execution.json()
    assert run["status"] == "succeeded"
    assert run["metrics"]["rows_processed"] == len(SALES_ROWS)

    result = client.get(f"{api}/results/{run['result_id']}").json()
    assert result["kind"] == "table"
    assert result["is_materialised"] is True

    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    assert len(payload["rows"]) == len(SALES_ROWS)
    for computed, expected in zip(payload["rows"], SALES_ROWS, strict=True):
        assert computed["revenue"] == pytest.approx(
            expected["price"] * expected["quantity"]
        )

    #  And the result is chartable without anyone knowing it began life as a CSV.
    chart = client.post(
        f"{api}/visualizations",
        json={
            "name": "Golden path revenue by product",
            "dataset_version_id": result["dataset_version_id"],
            "spec": {"chart_type": "bar", "x": "product", "y": ["revenue"],
                     "aggregation": "sum"},
        },
    )
    assert chart.status_code == 201, chart.text
    rendered = client.get(
        f"{api}/visualizations/{chart.json()['id']}/render"
    ).json()
    assert set(rendered["categories"]) == {"Widget", "Gadget"}
    assert rendered["series"][0]["data"]


def test_rule_model_classifies_rows(client, api, dataset):
    model = client.post(
        f"{api}/models",
        json={
            "name": "Golden path order class",
            "provider": "rule",
            "configuration": {
                "rules": [
                    {"name": "bulk", "when": "quantity >= 30",
                     "then": {"order_class": "BULK"}}
                ],
                "default": {"order_class": "STANDARD"},
            },
        },
    ).json()

    run = client.post(
        f"{api}/executions",
        json={"model_id": model["id"], "dataset_id": dataset["id"]},
    ).json()
    assert run["status"] == "succeeded"

    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    classes = [row["order_class"] for row in payload["rows"]]
    assert classes.count("BULK") == 1
    assert classes.count("STANDARD") == len(SALES_ROWS) - 1


def test_inline_input_needs_no_dataset(client, api):
    model = client.post(
        f"{api}/models",
        json={
            "name": "Inline revenue",
            "provider": "formula",
            "configuration": {"expressions": {"revenue": "price * quantity"}},
        },
    ).json()
    run = client.post(
        f"{api}/executions",
        json={
            "model_id": model["id"],
            "input": {"price": 10, "quantity": 3},
        },
    ).json()
    assert run["status"] == "succeeded"
    payload = client.get(f"{api}/results/{run['result_id']}/payload").json()["payload"]
    assert payload["revenue"] == 30
