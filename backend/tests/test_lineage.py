"""Two questions a platform that sells traceability has to be able to answer.

"Where did this number come from" and "what breaks if I change this source".
Every fact needed to answer them was already being written - a dataset version
records the execution that produced it, an execution records the model and the
version it read, a chart records what it was built from - and none of it could
be queried. The dicts were write-only.

The graph is derived rather than stored, so these tests also pin the thing that
makes that safe: it agrees with the rows. A stored edge table would need its
own tests for staying in step with them.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def traced(client, api) -> dict:
    """A whole chain: source -> dataset -> execution -> result -> chart -> board."""
    source = client.post(
        f"{api}/sources",
        json={
            "name": "lineage rows",
            "type": "inline",
            "connection": {
                "rows": [
                    {"city": "Taipei", "units": 3},
                    {"city": "Tainan", "units": 5},
                ]
            },
        },
    )
    assert source.status_code == 201, source.text

    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Lineage data", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text

    model = client.post(
        f"{api}/models",
        json={
            "name": "Lineage doubler",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "units * 2"}},
        },
    )
    assert model.status_code == 201, model.text

    execution = client.post(
        f"{api}/executions",
        json={
            "model_id": model.json()["id"],
            "dataset_id": dataset.json()["id"],
            "materialise_datasets": True,
        },
    )
    assert execution.status_code == 201, execution.text

    visualization = client.post(
        f"{api}/visualizations",
        json={
            "name": "Lineage chart",
            "dataset_id": dataset.json()["id"],
            "spec": {"chart_type": "bar", "x": "city", "y": ["units"]},
        },
    )
    assert visualization.status_code == 201, visualization.text

    dashboard = client.post(
        f"{api}/dashboards",
        json={
            "name": "Lineage board",
            "tiles": [{"visualization_id": visualization.json()["id"], "width": 6}],
        },
    )
    assert dashboard.status_code == 201, dashboard.text

    return {
        "source": source.json(),
        "dataset": dataset.json(),
        "model": model.json(),
        "execution": execution.json(),
        "visualization": visualization.json(),
        "dashboard": dashboard.json(),
    }


def _graph(client, api, kind: str, node_id: str, **params) -> dict:
    response = client.get(f"{api}/lineage/{kind}/{node_id}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _kinds(graph: dict) -> set[str]:
    return {node["kind"] for node in graph["nodes"]}


# --------------------------------------------------------------------------
# where did this come from
# --------------------------------------------------------------------------
def test_a_dashboard_can_say_where_its_numbers_came_from(client, api, traced):
    """The question the platform's own pitch implies it can answer."""
    graph = _graph(
        client, api, "dashboard", traced["dashboard"]["id"], direction="up", depth=6
    )

    assert _kinds(graph) >= {"dashboard", "visualization", "dataset_version", "dataset"}
    #  And it reaches all the way back to the file it was read from.
    assert "source" in _kinds(graph)

    labels = {node["label"] for node in graph["nodes"]}
    assert "Lineage board" in labels
    assert "lineage rows" in labels


def test_a_result_points_back_at_the_execution_and_the_model(client, api, traced):
    result_id = traced["execution"]["result_id"]
    graph = _graph(client, api, "result", result_id, direction="up", depth=4)

    assert _kinds(graph) >= {"result", "execution", "model"}
    relations = {edge["relation"] for edge in graph["edges"]}
    assert "produced" in relations
    assert "ran" in relations


def test_every_edge_points_at_a_node_in_the_graph(client, api, traced):
    """A graph with an edge to nothing renders as an arrow into empty space."""
    graph = _graph(
        client, api, "dashboard", traced["dashboard"]["id"], direction="up", depth=6
    )
    keys = {node["key"] for node in graph["nodes"]}
    dangling = [
        edge for edge in graph["edges"]
        if edge["from"] not in keys or edge["to"] not in keys
    ]
    assert not dangling, f"edges with no node: {dangling}"


# --------------------------------------------------------------------------
# what depends on this
# --------------------------------------------------------------------------
def test_a_source_can_say_what_would_break(client, api, traced):
    """The other half: "I am about to change this - what reads it?\""""
    graph = _graph(
        client, api, "source", traced["source"]["id"], direction="down", depth=6
    )

    assert _kinds(graph) >= {"source", "dataset", "dataset_version"}
    labels = {node["label"] for node in graph["nodes"]}
    assert "Lineage data" in labels


def test_a_model_lists_the_executions_that_ran_it(client, api, traced):
    graph = _graph(
        client, api, "model", traced["model"]["id"], direction="down", depth=2
    )
    execution_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "execution"}
    assert traced["execution"]["id"] in execution_ids


# --------------------------------------------------------------------------
# the shape of the answer
# --------------------------------------------------------------------------
def test_depth_bounds_the_walk_and_says_when_it_stopped(client, api, traced):
    shallow = _graph(
        client, api, "dashboard", traced["dashboard"]["id"], direction="up", depth=1
    )
    deep = _graph(
        client, api, "dashboard", traced["dashboard"]["id"], direction="up", depth=6
    )

    assert len(shallow["nodes"]) < len(deep["nodes"])
    #  A reader who stopped early has to be told there is more.
    assert shallow["truncated"] is True
    assert deep["truncated"] is False


def test_an_unknown_kind_is_refused_with_the_list_of_known_ones(client, api):
    response = client.get(f"{api}/lineage/teapot/x_1")
    assert response.status_code == 422, response.text
    assert "dataset" in response.text


def test_a_missing_node_is_a_404(client, api):
    response = client.get(f"{api}/lineage/dataset/ds_nope")
    assert response.status_code == 404, response.text


def test_a_bad_direction_is_refused(client, api, traced):
    response = client.get(
        f"{api}/lineage/dataset/{traced['dataset']['id']}", params={"direction": "sideways"}
    )
    assert response.status_code == 422, response.text


def test_a_dataset_built_by_a_run_is_not_a_dead_end(client, api, traced):
    """The case that appeared to have come from nowhere.

    A dataset materialised by an execution has no `source_id` - nothing was
    read to make it - so tracing upstream found nothing and the page said the
    trail started there. It does not: the version it holds was produced by an
    execution, and that execution ran a model on another dataset.

    The mistake underneath was treating "which versions does this dataset
    have" as a downstream-only step. Containment is followable both ways; only
    flow has a direction.
    """
    materialised = client.get(f"{api}/datasets", params={"include": "all"}).json()
    produced = next(
        (d for d in materialised if d["origin"] == "execution"), None
    )
    assert produced, "the fixture did not materialise a dataset"

    graph = _graph(client, api, "dataset", produced["id"], direction="up", depth=6)

    kinds = _kinds(graph)
    assert "dataset_version" in kinds, "its own versions are not upstream of it"
    assert "execution" in kinds, "the execution that produced it is missing"
    assert "model" in kinds, "the model that ran is missing"


def test_containment_is_drawn_the_way_the_data_flowed(client, api, traced):
    """Whichever way it is walked, a version points at its dataset.

    An edge that flips direction depending on how it was reached draws two
    different pictures of one fact.
    """
    up = _graph(client, api, "dataset", traced["dataset"]["id"], direction="up", depth=3)
    down = _graph(
        client, api, "dataset", traced["dataset"]["id"], direction="down", depth=3
    )

    def containment(graph):
        return {
            (edge["from"].split(":")[0], edge["to"].split(":")[0])
            for edge in graph["edges"]
            if edge["relation"] == "version of"
        }

    assert containment(up) == {("dataset_version", "dataset")}
    assert containment(down) == {("dataset_version", "dataset")}
