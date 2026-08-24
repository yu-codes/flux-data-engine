"""Calling a model the way another system would.

`POST /executions` answers "what ran, when, and what did it produce" - which is
the right question for batch work and the wrong one for a service calling a
model a few times a second. That caller wants the answer and nothing else, and
it wants to authenticate as itself rather than as somebody's account.

The properties worth pinning are that invoking gives the same answer as
submitting, that it leaves nothing behind, and that a key is a narrower
credential than a password rather than a more convenient one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def model(client, api) -> dict:
    created = client.post(
        f"{api}/models",
        json={
            "name": "Serving revenue",
            "provider": "formula",
            "configuration": {"expressions": {"revenue": "price * quantity"}},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


# --------------------------------------------------------------------------
# the same answer, without the record
# --------------------------------------------------------------------------
def test_invoking_returns_the_answer_directly(client, api, model):
    response = client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 10, "quantity": 3}},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["model_id"] == model["id"]
    assert body["kind"] == "calculation"
    assert body["duration_seconds"] >= 0
    #  A formula over one record answers with an object, not a table: the
    #  shape follows the input, exactly as it does for a submitted execution.
    assert _revenue(body) == 30


def test_invoking_and_submitting_agree(client, api, model):
    """If the two disagreed, one of them would be lying about what the model does."""
    invoked = client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 4, "quantity": 5}},
    ).json()

    submitted = client.post(
        f"{api}/executions",
        json={
            "model_id": model["id"],
            "kind": "calculation",
            "input": {"price": 4, "quantity": 5},
        },
    )
    assert submitted.status_code == 201, submitted.text
    result = client.get(f"{api}/results/{submitted.json()['result_id']}/payload").json()

    assert _revenue(invoked) == 20
    assert _revenue(result) == 20


def test_invoking_records_nothing(client, api, model):
    """The whole point: no Execution, no Result, no dataset."""
    before = len(client.get(f"{api}/executions?limit=200").json())
    client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 1, "quantity": 1}},
    )
    client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 2, "quantity": 2}},
    )
    after = len(client.get(f"{api}/executions?limit=200").json())
    assert after == before


def test_contracts_are_applied_the_same_way(client, api):
    """Same contracts. An endpoint that skipped them would be a second model.

    Checked against a provider that declares one: the formula provider's input
    contract is deliberately open, so it would accept anything here and prove
    nothing either way.
    """
    strict = client.post(
        f"{api}/models",
        json={
            "name": "Serving strict transform",
            "provider": "python-transform",
            "configuration": {
                "transform": "limit_rows",
                "options": {"count": 2},
            },
        },
    )
    assert strict.status_code == 201, strict.text

    #  `count` must be at least one; the transform says so and invoke listens.
    response = client.post(
        f"{api}/models/{strict.json()['id']}/invoke",
        json={
            "input": {"rows": [{"a": 1}, {"a": 2}, {"a": 3}]},
            "parameters": {"count": 0},
        },
    )
    assert response.status_code in (400, 422), response.text


def test_an_unknown_model_cannot_be_invoked(client, api):
    response = client.post(f"{api}/models/mdl_nope/invoke", json={"input": {}})
    assert response.status_code == 404, response.text


def test_training_cannot_be_invoked(client, api):
    """It publishes an immutable version, which is a change, not an answer."""
    trainable = client.post(
        f"{api}/models",
        json={
            "name": "Serving trainable",
            "provider": "sklearn",
            "configuration": {
                "algorithm": "linear_regression",
                "features": ["price"],
                "target": "revenue",
            },
        },
    )
    assert trainable.status_code == 201, trainable.text

    response = client.post(
        f"{api}/models/{trainable.json()['id']}/invoke", json={"kind": "training"}
    )
    assert response.status_code == 400, response.text
    assert "cannot be invoked" in response.text


# --------------------------------------------------------------------------
# authenticating as a system
# --------------------------------------------------------------------------
@pytest.fixture
def issued(client, api) -> dict:
    created = client.post(
        f"{api}/api-keys", json={"name": "integration test key"}
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_a_key_is_shown_once_and_stored_only_as_a_hash(client, api, issued):
    assert issued["secret"].startswith("flux_")
    assert issued["hint"] and issued["hint"] in issued["secret"]

    #  Listing keys shows the hint, never the secret.
    listed = client.get(f"{api}/api-keys").json()
    mine = next(k for k in listed if k["id"] == issued["id"])
    assert "secret" not in mine
    assert mine["hint"] == issued["hint"]


def test_a_key_authenticates_an_invocation(anonymous, api, model, issued):
    """No password, no login, no session."""
    response = anonymous.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 6, "quantity": 7}},
        headers={"X-Api-Key": issued["secret"]},
    )
    assert response.status_code == 200, response.text
    assert _revenue(response.json()) == 42


def test_no_credential_at_all_is_refused(anonymous, api, model):
    response = anonymous.post(
        f"{api}/models/{model['id']}/invoke", json={"input": {"price": 1, "quantity": 1}}
    )
    assert response.status_code == 401, response.text


def test_a_made_up_key_is_refused(anonymous, api, model):
    response = anonymous.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 1, "quantity": 1}},
        headers={"X-Api-Key": "flux_not_a_real_key"},
    )
    assert response.status_code == 401, response.text


def test_a_revoked_key_stops_working(client, anonymous, api, model, issued):
    revoked = client.post(f"{api}/api-keys/{issued['id']}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["is_active"] is False

    response = anonymous.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"price": 1, "quantity": 1}},
        headers={"X-Api-Key": issued["secret"]},
    )
    assert response.status_code == 401, response.text


def test_a_key_sees_only_its_own_workspace(client, anonymous, api, issued):
    """A key names its workspace, so a header cannot point it at another."""
    other = client.post(f"{api}/workspaces", json={"name": "Serving elsewhere"})
    assert other.status_code == 201, other.text

    hidden = client.post(
        f"{api}/models",
        json={
            "name": "Only over there",
            "provider": "formula",
            "configuration": {"expressions": {"x": "1"}},
        },
        headers={"X-Workspace": other.json()["id"]},
    )
    assert hidden.status_code == 201, hidden.text

    #  Even asking for the other workspace explicitly.
    listed = anonymous.get(
        f"{api}/models",
        headers={"X-Api-Key": issued["secret"], "X-Workspace": other.json()["id"]},
    )
    assert listed.status_code == 200, listed.text
    assert hidden.json()["id"] not in {m["id"] for m in listed.json()}


def _revenue(body: dict) -> float:
    """The answer, whichever shape the payload carried it in."""
    if body.get("rows"):
        return body["rows"][0]["revenue"]
    for key in ("value", "inline_payload", "payload"):
        value = body.get(key)
        if isinstance(value, dict) and "revenue" in value:
            return value["revenue"]
    if "revenue" in body:
        return body["revenue"]
    raise AssertionError(f"no revenue in {body}")


def test_a_large_answer_comes_back_as_a_page_with_its_count(client, api):
    """Serving is not a bulk export, and should not pretend to be.

    A model whose output is a table of forty thousand rows used to put all of
    them in the response body of a call meant to answer in milliseconds. The
    first page plus an honest count is the right answer; the whole table is
    what `POST /executions` is for.
    """
    source = client.post(
        f"{api}/sources",
        json={
            "name": "serving volume rows",
            "type": "inline",
            "connection": {"rows": [{"n": i} for i in range(2_500)]},
        },
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Serving volume", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text

    model = client.post(
        f"{api}/models",
        json={
            "name": "Serving volume model",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "n * 2"}},
        },
    )
    assert model.status_code == 201, model.text

    response = client.post(
        f"{api}/models/{model.json()['id']}/invoke",
        json={"dataset_version_id": dataset.json()["current_version_id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["row_count"] == 2_500
    assert body["truncated"] is True
    assert len(body["rows"]) == 1_000


def test_an_answer_that_fits_is_not_marked_truncated(client, api, model):
    response = client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"input": {"rows": [{"price": 3, "quantity": 4},
                                {"price": 1, "quantity": 2}]}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is False
    assert body["row_count"] == 2


def test_invoking_accepts_a_dataset_id_like_submitting_does(client, api, model):
    """A caller holds a dataset id far more often than a version id.

    `POST /executions` has always taken either. `invoke` took only the version,
    and pydantic ignores fields it does not know — so a caller who sent
    `dataset_id` got a successful response computed from no input at all,
    which is the worst way for an API to disagree with its sibling.
    """
    source = client.post(
        f"{api}/sources",
        json={
            "name": "serving by dataset",
            "type": "inline",
            "connection": {"rows": [{"price": 2, "quantity": 3}]},
        },
    )
    assert source.status_code == 201, source.text
    dataset = client.post(
        f"{api}/datasets",
        json={"name": "Serving by dataset", "source_id": source.json()["id"]},
    )
    assert dataset.status_code == 201, dataset.text

    response = client.post(
        f"{api}/models/{model['id']}/invoke",
        json={"dataset_id": dataset.json()["id"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["row_count"] == 1
    assert _revenue(body) == 6
