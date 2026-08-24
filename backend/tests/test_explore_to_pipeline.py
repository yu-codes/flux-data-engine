"""Explore is where somebody works out what they want. It had nowhere to go.

The filter conditions and the sort worked out on screen could not be kept: the
only way to reuse them was to open the pipeline builder and set the same thing
again from memory, which is both tedious and the moment the two versions start
disagreeing.

The translation is tested here rather than in the browser because it is
knowledge about transforms - which one implements a condition, what it calls
its options - and that is worth pinning where it can be read.
"""

from __future__ import annotations

import pytest

from app.modules.orchestration.application.from_query import steps_from_query
from app.shared.errors import ValidationError


# --------------------------------------------------------------------------
# the translation
# --------------------------------------------------------------------------
def test_a_filter_becomes_a_filter_step():
    steps = steps_from_query(
        filters=[{"column": "city", "op": "eq", "value": "Taipei"}]
    )

    assert len(steps) == 1
    step = steps[0]
    assert step["provider"] == "python-transform"
    assert step["configuration"]["transform"] == "filter_rows"
    #  Explore says "eq"; the transform says "equals". Both are reasonable and
    #  neither had to change to suit the other.
    assert step["configuration"]["options"] == {
        "column": "city",
        "op": "equals",
        "value": "Taipei",
    }


def test_a_sort_becomes_a_sort_step():
    steps = steps_from_query(sort_by="units", sort_desc=True)

    assert steps[0]["configuration"]["transform"] == "sort_rows"
    assert steps[0]["configuration"]["options"] == {
        "column": "units",
        "descending": True,
    }


def test_the_order_is_the_order_a_person_would_read():
    """Columns, then rows, then the order of what is left."""
    steps = steps_from_query(
        columns=["city", "units"],
        filters=[{"column": "units", "op": "gt", "value": 3}],
        sort_by="units",
    )
    assert [s["configuration"]["transform"] for s in steps] == [
        "select_columns",
        "filter_rows",
        "sort_rows",
    ]


def test_an_empty_condition_is_skipped_not_turned_into_a_broken_step():
    """Explore always keeps one blank row on screen for the next condition."""
    steps = steps_from_query(
        filters=[{"column": None, "op": "eq", "value": ""},
                 {"column": "city", "op": "not_null"}],
        sort_by=None,
    )
    assert len(steps) == 1
    assert steps[0]["configuration"]["options"] == {"column": "city", "op": "not_empty"}


def test_a_list_typed_as_text_becomes_a_list():
    """`in` is typed comma-separated on screen and is a list in the contract."""
    steps = steps_from_query(
        filters=[{"column": "city", "op": "in", "value": "Taipei, Tainan"}]
    )
    assert steps[0]["configuration"]["options"]["value"] == ["Taipei", "Tainan"]


def test_an_operator_a_step_cannot_express_is_refused_by_name():
    """`contains` has no `filter_rows` equivalent.

    Dropping it silently would build a pipeline that filters less than the
    screen did, and nothing would say so - the answer would just be quietly
    wrong from then on.
    """
    with pytest.raises(ValidationError, match="contains"):
        steps_from_query(filters=[{"column": "note", "op": "contains", "value": "x"}])


def test_saving_nothing_is_refused():
    with pytest.raises(ValidationError, match="nothing to save"):
        steps_from_query()


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def dataset(client, api) -> dict:
    source = client.post(
        f"{api}/sources",
        json={
            "name": "explore handoff rows",
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
        json={"name": "Explore handoff", "source_id": source.json()["id"]},
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_query_can_be_kept_as_a_pipeline_and_it_runs(client, api, dataset):
    """The whole point: what is on screen becomes something repeatable."""
    created = client.post(
        f"{api}/pipelines/from-query",
        json={
            "name": "Busy cities",
            "dataset_id": dataset["id"],
            "filters": [{"column": "units", "op": "gt", "value": 2}],
            "sort_by": "units",
            "sort_desc": True,
        },
    )
    assert created.status_code == 201, created.text
    pipeline = created.json()
    assert [s["name"] for s in pipeline["steps"]] == [
        "keep where units gt",
        "order by units",
    ]

    run = client.post(f"{api}/pipelines/{pipeline['id']}/run", json={})
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "succeeded", run.json().get("error")

    #  And it answers what Explore was showing: two cities, biggest first.
    version = run.json()["output_dataset_ids"]
    assert version, "the pipeline produced no dataset"
    rows = client.get(
        f"{api}/datasets/{version[0]}/preview", params={"limit": 10}
    ).json()["rows"]
    assert [row["city"] for row in rows] == ["Tainan", "Taipei"]


def test_a_query_that_expresses_nothing_is_refused_with_a_reason(client, api, dataset):
    response = client.post(
        f"{api}/pipelines/from-query",
        json={"name": "Nothing at all", "dataset_id": dataset["id"]},
    )
    assert response.status_code == 422, response.text
    assert "nothing to save" in response.text


def test_the_steps_form_a_chain_not_three_branches():
    """Leaving `input_from` unset means "the pipeline's input dataset".

    Every step would then read the source and ignore the others: a filter that
    filters nothing the sort can see, and one output dataset per step instead
    of one for the pipeline. The run still succeeds, which is what makes it
    worth a test - the answer is simply wrong.
    """
    steps = steps_from_query(
        columns=["city", "units"],
        filters=[{"column": "units", "op": "gt", "value": 2}],
        sort_by="units",
    )

    assert steps[0].get("input_from") is None, "the first step reads the dataset"
    assert [s.get("input_from") for s in steps[1:]] == [
        "keep the chosen columns",
        "keep where units gt",
    ]
