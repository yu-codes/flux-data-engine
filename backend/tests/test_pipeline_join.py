"""A pipeline graph that merges, not only branches.

The graph could branch from the beginning, and the reason it could not merge
was stated honestly in the module docstring: every provider read exactly one
table, so a join had no shape to take. That made the most common operation in
data work inexpressible.

What is pinned here is the whole path - a step may name several upstreams, the
ordering waits for all of them, the runner hands them over by name, and a
provider reads more than one.
"""

from __future__ import annotations

import pytest

ORDERS = [
    {"order_id": 1, "product": "Widget", "quantity": 4},
    {"order_id": 2, "product": "Gadget", "quantity": 2},
    {"order_id": 3, "product": "Widget", "quantity": 7},
    {"order_id": 4, "product": "Sprocket", "quantity": 1},
]
PRICES = [
    {"product": "Widget", "price": 25.0},
    {"product": "Gadget", "price": 60.0},
]


def _inline(client, api, name: str, rows: list[dict]) -> str:
    source = client.post(
        f"{api}/sources",
        json={"name": name, "type": "inline", "connection": {"rows": rows}},
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets", json={"name": name.title(), "source_id": source.json()["id"]}
    )
    assert dataset.status_code == 201, dataset.text
    return dataset.json()["id"]


@pytest.fixture(scope="module")
def orders(client, api) -> str:
    return _inline(client, api, "join orders", ORDERS)


@pytest.fixture(scope="module")
def prices(client, api) -> str:
    return _inline(client, api, "join prices", PRICES)


@pytest.fixture(scope="module")
def merged(client, api, orders, prices) -> dict:
    """Two branches from one input, rejoined by a third step."""
    created = client.post(
        f"{api}/pipelines",
        json={
            "name": "Orders joined to prices",
            "input_dataset_id": orders,
            "steps": [
                #  The chain carries the orders.
                {
                    "name": "orders",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "select_columns",
                        "options": {"columns": ["order_id", "product", "quantity"]},
                    },
                },
                #  And here it merges with a reference table that is not
                #  derived from the pipeline's input at all - which is what
                #  a real join is almost always against.
                {
                    "name": "priced",
                    "provider": "join",
                    "configuration": {"on": ["product"], "how": "left"},
                    "input_from": "orders",
                    "input_datasets": {"right": prices},
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------
def test_a_step_may_declare_several_upstreams(client, api, merged):
    graph = client.get(f"{api}/pipelines/{merged['id']}/graph").json()
    edges = {(e["from"], e["to"]) for e in graph["edges"]}
    assert ("__input__", "orders") in edges
    assert ("orders", "priced") in edges


def test_only_the_merged_step_is_terminal(client, api, merged):
    """A step feeding a join is not an output nobody asked for."""
    graph = client.get(f"{api}/pipelines/{merged['id']}/graph").json()
    assert graph["terminal_steps"] == ["priced"]


def test_a_cycle_through_a_second_input_is_still_a_cycle(client, api, orders):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Join cycle",
            "input_dataset_id": orders,
            "steps": [
                {
                    "name": "a",
                    "provider": "join",
                    "configuration": {"on": ["product"]},
                    "inputs": {"right": "b"},
                },
                {
                    "name": "b",
                    "provider": "join",
                    "configuration": {"on": ["product"]},
                    "inputs": {"right": "a"},
                },
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert "cycle" in response.json()["message"]


def test_an_unknown_second_input_is_rejected(client, api, orders):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Join to nowhere",
            "input_dataset_id": orders,
            "steps": [
                {
                    "name": "priced",
                    "provider": "join",
                    "configuration": {"on": ["product"]},
                    "inputs": {"right": "no such step"},
                }
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert "not a step in this pipeline" in response.json()["message"]


# --------------------------------------------------------------------------
# the join itself
# --------------------------------------------------------------------------
def test_running_the_pipeline_merges_the_two_branches(client, api, merged):
    run = client.post(f"{api}/pipelines/{merged['id']}/run", json={})
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["status"] == "succeeded", body.get("error")

    priced = next(s for s in body["step_runs"] if s["step_name"] == "priced")
    preview = client.get(
        f"{api}/dataset-versions/{priced['dataset_version_id']}/preview?limit=20"
    ).json()

    columns = {c["name"] for c in preview["columns"]}
    assert {"order_id", "product", "quantity", "price"} <= columns

    by_order = {row["order_id"]: row for row in preview["rows"]}
    #  A left join keeps every order.
    assert set(by_order) == {1, 2, 3, 4}
    assert by_order[1]["price"] == 25.0
    assert by_order[2]["price"] == 60.0
    #  Sprocket has no price, and says so rather than disappearing.
    assert by_order[4]["price"] is None


def test_the_join_reports_what_it_did(client, api, merged):
    runs = client.get(f"{api}/pipeline-runs?pipeline_id={merged['id']}").json()
    priced = next(s for s in runs[0]["step_runs"] if s["step_name"] == "priced")
    execution = client.get(f"{api}/executions/{priced['execution_id']}").json()

    metrics = execution["metrics"]
    assert metrics["rows_left"] == len(ORDERS)
    assert metrics["rows_out"] == len(ORDERS)


def test_a_join_without_its_second_table_says_so(client, api, orders):
    """The error names the missing input rather than joining against nothing."""
    model = client.post(
        f"{api}/models",
        json={
            "name": "Lonely join",
            "provider": "join",
            "configuration": {"on": ["product"]},
        },
    )
    assert model.status_code == 201, model.text

    execution = client.post(
        f"{api}/executions",
        json={
            "model_id": model.json()["id"],
            "kind": "transformation",
            "dataset_id": orders,
        },
    )
    assert execution.status_code in (400, 422), execution.text
    assert "right" in execution.text


def test_join_keys_must_exist_in_both_tables(client, api, orders, prices):
    response = client.post(
        f"{api}/pipelines",
        json={
            "name": "Join on a missing key",
            "input_dataset_id": orders,
            "steps": [
                {
                    "name": "orders",
                    "provider": "python-transform",
                    "configuration": {
                        "transform": "select_columns",
                        "options": {"columns": ["order_id", "product"]},
                    },
                },
                {
                    "name": "priced",
                    "provider": "join",
                    "configuration": {"on": ["nonexistent"]},
                    "input_from": "orders",
                    "inputs": {"right": "orders"},
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    run = client.post(f"{api}/pipelines/{response.json()['id']}/run", json={}).json()
    assert run["status"] == "failed"
    assert "nonexistent" in (run["error"] or "") or any(
        "nonexistent" in (s["error"] or "") for s in run["step_runs"]
    )


def test_a_join_with_no_keys_is_refused_when_the_model_is_created(client, api):
    response = client.post(
        f"{api}/models",
        json={"name": "Keyless join", "provider": "join", "configuration": {"on": []}},
    )
    assert response.status_code == 422, response.text
    assert "key column" in response.text
