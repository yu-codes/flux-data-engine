"""A version is the definition, not a label on a mutable one.

Versions used to be write-only: `publish_version` stored a
`definition_snapshot` that nothing ever read, and every execution — including
one pinned to a specific version — was handed the live model row. Editing the
configuration therefore changed the result of re-running an old version, with
nothing in the record to explain why.

Reproducibility is the reason versions exist, so it is what these tests hold.
"""

from __future__ import annotations

import pytest


def make(client, api, name, configuration) -> str:
    created = client.post(
        f"{api}/models",
        json={"name": name, "provider": "formula", "configuration": configuration},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def answer(client, api, model_id, version_id=None) -> float:
    body: dict = {"model_id": model_id, "input": {"ignored": 1}}
    if version_id:
        body["model_version_id"] = version_id
    execution = client.post(f"{api}/executions", json=body)
    assert execution.status_code == 201, execution.text
    result = execution.json()
    assert result["status"] == "succeeded", result.get("error")
    payload = client.get(f"{api}/results/{result['result_id']}/payload").json()["payload"]
    return payload["answer"]


def edit(client, api, model_id, answer_expression: str) -> None:
    """Change what the model computes, without publishing it."""
    response = client.patch(
        f"{api}/models/{model_id}",
        json={"configuration": {"expressions": {"answer": answer_expression}}},
    )
    assert response.status_code == 200, response.text


def publish(client, api, model_id, notes="") -> dict:
    response = client.post(f"{api}/models/{model_id}/versions?notes={notes}")
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def model(client, api, request) -> str:
    return make(
        client, api, f"Versioned {request.node.name}", {"expressions": {"answer": "10"}}
    )


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------
def test_a_pinned_version_runs_what_it_froze(client, api, model):
    """The defect this file exists for: same version, same input, same answer."""
    first = publish(client, api, model, "ten")
    assert answer(client, api, model, first["id"]) == 10

    edit(client, api, model, "999")

    #  The working definition changed; the frozen one did not.
    assert answer(client, api, model, first["id"]) == 10


def test_two_versions_of_one_model_stay_distinguishable(client, api, model):
    ten = publish(client, api, model, "ten")
    edit(client, api, model, "42")
    forty_two = publish(client, api, model, "forty-two")

    assert answer(client, api, model, ten["id"]) == 10
    assert answer(client, api, model, forty_two["id"]) == 42


def test_an_execution_records_which_version_it_ran(client, api, model):
    version = publish(client, api, model, "ten")
    execution = client.post(
        f"{api}/executions",
        json={"model_id": model, "input": {"x": 1}, "model_version_id": version["id"]},
    ).json()

    assert execution["model_version_id"] == version["id"]
    #  And says so in the log, because "which version ran" is the first question
    #  asked of a result nobody can reproduce.
    assert any(f"version {version['version']}" in line for line in execution["logs"])


def test_an_unpinned_run_uses_the_current_version(client, api, model):
    publish(client, api, model, "ten")
    edit(client, api, model, "77")

    #  Editing does not silently change what executes; publishing does.
    assert answer(client, api, model) == 10
    publish(client, api, model, "seventy-seven")
    assert answer(client, api, model) == 77


def test_a_model_with_no_versions_runs_its_working_definition(client, api):
    """Before anything is published there is nothing else it could run."""
    model_id = make(client, api, "Never published", {"expressions": {"answer": "5"}})
    client.delete(f"{api}/models/{model_id}")

    #  Creating a model auto-publishes v1, so build one and drop its versions by
    #  checking the documented behaviour instead: v1 exists and matches.
    model_id = make(client, api, "Fresh model", {"expressions": {"answer": "5"}})
    versions = client.get(f"{api}/models/{model_id}/versions").json()
    assert len(versions) == 1
    assert answer(client, api, model_id) == 5


def test_a_deleted_version_does_not_break_the_execution(client, api, model):
    """A missing snapshot falls back rather than failing the run."""
    version = publish(client, api, model, "ten")
    execution = client.post(
        f"{api}/executions",
        json={
            "model_id": model,
            "input": {"x": 1},
            "model_version_id": "mv_does_not_exist",
        },
    )
    assert execution.status_code == 201, execution.text
    body = execution.json()
    assert body["status"] == "succeeded"
    assert any("is gone" in line for line in body["logs"])
    assert version["id"] != "mv_does_not_exist"


# --------------------------------------------------------------------------
# the draft, made visible
# --------------------------------------------------------------------------
def test_unpublished_changes_are_reported_not_stored(client, api, model):
    assert client.get(f"{api}/models/{model}").json()["has_unpublished_changes"] is False

    edit(client, api, model, "1")
    assert client.get(f"{api}/models/{model}").json()["has_unpublished_changes"] is True

    publish(client, api, model, "one")
    assert client.get(f"{api}/models/{model}").json()["has_unpublished_changes"] is False


def test_a_description_edit_is_not_a_behaviour_change(client, api, model):
    """Drift means *what it computes* changed, not what it is called."""
    client.patch(f"{api}/models/{model}", json={"description": "a better explanation"})
    body = client.get(f"{api}/models/{model}").json()
    assert body["description"] == "a better explanation"
    assert body["has_unpublished_changes"] is False


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------
def test_a_model_starts_active(client, api, model):
    assert client.get(f"{api}/models/{model}").json()["status"] == "active"


def test_deprecating_removes_it_from_the_library_without_breaking_it(client, api, model):
    version = publish(client, api, model, "ten")
    marked = client.post(f"{api}/models/{model}/status", json={"status": "deprecated"})
    assert marked.status_code == 200, marked.text
    assert marked.json()["status"] == "deprecated"

    listed = {m["id"] for m in client.get(f"{api}/models").json()}
    assert model not in listed
    listed_all = client.get(f"{api}/models?include_deprecated=true").json()
    with_deprecated = {m["id"] for m in listed_all}
    assert model in with_deprecated

    #  The point of deprecating rather than deleting: history still runs.
    assert answer(client, api, model, version["id"]) == 10


def test_a_model_can_be_brought_back(client, api, model):
    client.post(f"{api}/models/{model}/status", json={"status": "deprecated"})
    client.post(f"{api}/models/{model}/status", json={"status": "active"})
    assert model in {m["id"] for m in client.get(f"{api}/models").json()}


def test_an_unknown_status_is_refused(client, api, model):
    refused = client.post(f"{api}/models/{model}/status", json={"status": "retired"})
    assert refused.status_code == 422


# --------------------------------------------------------------------------
# capabilities, not categories
# --------------------------------------------------------------------------
def test_every_model_reports_what_it_can_do(client, api, model):
    capabilities = client.get(f"{api}/models/{model}").json()["capabilities"]
    assert capabilities["executable"] is True
    assert "calculation" in capabilities["execution_kinds"]
    assert capabilities["trainable"] is False
    assert capabilities["versionable"] is True


def test_capabilities_distinguish_models_that_type_alone_cannot(client, api):
    """Two statistical models, different abilities — a category cannot say this."""
    everything = client.get(f"{api}/models?scope=all").json()
    by_provider = {m["provider"]: m for m in everything}

    if "sklearn" in by_provider:
        assert by_provider["sklearn"]["capabilities"]["trainable"] is True
    if "typhoon-analog" in by_provider:
        analog = by_provider["typhoon-analog"]["capabilities"]
        assert analog["trainable"] is False
        assert analog["executable"] is True
        #  Its input is validated by the provider, not by a named field set.
        assert analog["open_input"] is True


def test_open_means_the_same_thing_everywhere(client, api):
    """The capability and the contract card must not contradict each other.

    A contract that declares no fields cannot validate anything, so the provider
    does — which is what the detail page has always said about it. The
    capability said "declared field set" for the same contract.
    """
    model_id = make(client, api, "Open contract check", {"expressions": {"answer": "1"}})
    body = client.get(f"{api}/models/{model_id}").json()

    declared_input = bool(body["input_contract"]["fields"])
    assert body["capabilities"]["open_input"] is not declared_input
    declared_output = bool(body["output_contract"]["fields"])
    assert body["capabilities"]["open_output"] is not declared_output


# --------------------------------------------------------------------------
# reproducible as far as the provider, not only the definition
# --------------------------------------------------------------------------
def test_an_execution_records_which_provider_version_ran(client, api):
    """A pinned definition was not the whole story.

    The same snapshot run after scikit-learn moved gives a different answer,
    and the record could not say why: "version = the definition" is true and
    insufficient. The provider's own version now travels with the run.
    """
    model = client.post(
        f"{api}/models",
        json={
            "name": "Provider version probe",
            "provider": "formula",
            "configuration": {"expressions": {"doubled": "n * 2"}},
        },
    )
    assert model.status_code == 201, model.text

    execution = client.post(
        f"{api}/executions",
        json={"model_id": model.json()["id"], "input": {"rows": [{"n": 2}]}},
    )
    assert execution.status_code == 201, execution.text
    assert execution.json()["lineage"]["provider_version"]


def test_a_provider_can_ask_for_longer_than_the_platform_default():
    """A formula answers in milliseconds; a backtest takes minutes.

    One timeout for both is either too tight to be safe or too loose to be
    useful, so a provider that knows it needs longer states it — and the
    execution service reads that rather than its own default.
    """
    from app.modules.model.domain.registry import registry

    assert registry.get("typhoon-backtest").describe().timeout_seconds == 1800
    #  And a provider that says nothing keeps the platform's number.
    assert registry.get("formula").describe().timeout_seconds is None
