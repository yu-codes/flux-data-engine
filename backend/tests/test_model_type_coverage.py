"""Every model type the platform advertises must be one you can actually use.

The Model Library shows a provider grid built from `ModelType`. Three of its
eight categories — mathematical, optimization, simulation — read "none yet",
which is a promise the product could not keep: the category was offered and
nothing could be created in it.

These tests hold the claim to the implementation, and exercise each new provider
end to end through the ordinary Execution path.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.core.config import get_settings

#  y = 3x + 5 exactly, so the fit has a known right answer.
LINE = [{"x": float(i), "y": 3.0 * i + 5.0} for i in range(1, 11)]


@pytest.fixture(scope="module")
def line_dataset(client, api) -> str:
    settings = get_settings()
    relative = "Demo/sources/test_line.csv"
    path = Path(settings.data_root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["x", "y"])
        writer.writeheader()
        writer.writerows(LINE)

    source = client.post(
        f"{api}/sources",
        json={"name": "line rows", "type": "csv", "connection": {"path": relative}},
    )
    assert source.status_code == 201, source.text
    created = client.post(
        f"{api}/datasets", json={"name": "Line rows", "source_id": source.json()["id"]}
    )
    assert created.status_code == 201, created.text
    return created.json()["versions"][0]["id"]


def make(client, api, name, provider, configuration) -> str:
    created = client.post(
        f"{api}/models",
        json={"name": name, "provider": provider, "configuration": configuration},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def rejected(client, api, name, provider, configuration) -> list[str]:
    """Create a model that should not be accepted, and return why.

    The platform validates at creation rather than storing something broken and
    reporting it later — error prevention over error reporting — so a bad
    configuration never becomes a model at all.
    """
    response = client.post(
        f"{api}/models",
        json={"name": name, "provider": provider, "configuration": configuration},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["details"]["valid"] is False
    return body["details"]["errors"]


def run(client, api, model_id, **body) -> dict:
    execution = client.post(f"{api}/executions", json={"model_id": model_id, **body})
    assert execution.status_code == 201, execution.text
    result = execution.json()
    assert result["status"] == "succeeded", result.get("error")
    return result


# --------------------------------------------------------------------------
# the promise
# --------------------------------------------------------------------------
def test_every_advertised_model_type_has_a_provider(client, api):
    """No category may be listed with nothing behind it."""
    body = client.get(f"{api}/model-types").json()
    empty = [entry["type"] for entry in body["types"] if not entry["providers"]]
    assert not empty, f"advertised with no provider: {empty}"


def test_the_three_new_providers_are_registered(client, api):
    listed = client.get(f"{api}/model-providers").json()["providers"]
    providers = {p["key"]: p for p in listed}
    assert providers["curve-fit"]["model_type"] == "mathematical"
    assert providers["optimizer"]["model_type"] == "optimization"
    assert providers["monte-carlo"]["model_type"] == "simulation"
    #  None of them trains: they are models without being machine learning.
    new = ("curve-fit", "optimizer", "monte-carlo")
    assert not any(providers[k]["trainable"] for k in new)


# --------------------------------------------------------------------------
# mathematical: curve fit
# --------------------------------------------------------------------------
def test_a_linear_fit_recovers_the_line_it_was_given(client, api, line_dataset):
    model = make(
        client, api, "Fit the line", "curve-fit",
        {"x": "x", "y": "y", "family": "linear", "predict_for": [100]},
    )
    outcome = run(client, api, model, dataset_version_id=line_dataset)

    assert outcome["metrics"]["r_squared"] == 1.0
    assert outcome["metrics"]["rmse"] == 0.0
    coefficients = outcome["metrics"]["coefficients"]
    assert round(coefficients["slope"], 6) == 3.0
    assert round(coefficients["intercept"], 6) == 5.0

    payload = client.get(f"{api}/results/{outcome['result_id']}/payload").json()["payload"]
    assert len(payload["rows"]) == len(LINE)
    assert payload["rows"][0]["residual"] == 0.0


def test_a_curve_fit_reports_its_equation_and_prediction(client, api, line_dataset):
    model = make(
        client, api, "Fit and extrapolate", "curve-fit",
        {"x": "x", "y": "y", "family": "linear", "predict_for": [100]},
    )
    outcome = run(client, api, model, dataset_version_id=line_dataset)
    summary = client.get(f"{api}/results/{outcome['result_id']}").json()["summary"]

    assert "y = 3" in summary["equation"]
    #  3 × 100 + 5
    assert summary["predictions"][0]["predicted"] == 305.0


def test_an_impossible_polynomial_degree_is_refused(client, api):
    errors = rejected(
        client, api, "Impossible polynomial", "curve-fit",
        {"x": "x", "y": "y", "family": "polynomial", "degree": 99},
    )
    assert any("degree" in message for message in errors)


def test_a_curve_fit_refuses_a_single_record(client, api):
    model = make(client, api, "Fit one point", "curve-fit", {"x": "x", "y": "y"})
    execution = client.post(
        f"{api}/executions", json={"model_id": model, "input": {"x": 1, "y": 2}}
    )
    assert execution.status_code == 422
    assert "dataset" in execution.json()["message"]


# --------------------------------------------------------------------------
# optimization: grid search
# --------------------------------------------------------------------------
def test_the_optimiser_finds_the_known_optimum(client, api):
    """Revenue = price × (500 − 3·price) peaks at price ≈ 83.3."""
    model = make(
        client, api, "Best price", "optimizer",
        {
            "objective": "price * demand",
            "goal": "maximise",
            "variables": {"price": {"min": 10, "max": 200, "step": 1}},
            "derived": {"demand": "max(0, 500 - 3 * price)"},
        },
    )
    outcome = run(client, api, model, kind="optimization")

    best = client.get(f"{api}/results/{outcome['result_id']}").json()["summary"]["best"]
    assert best["price"] in (83.0, 84.0)
    assert outcome["metrics"]["evaluated"] == 191


def test_constraints_remove_candidates_rather_than_the_run(client, api):
    model = make(
        client, api, "Constrained price", "optimizer",
        {
            "objective": "price * demand",
            "variables": {"price": {"min": 10, "max": 200, "step": 1}},
            "derived": {"demand": "max(0, 500 - 3 * price)"},
            "constraints": ["price >= 120"],
        },
    )
    outcome = run(client, api, model, kind="optimization")
    best = client.get(f"{api}/results/{outcome['result_id']}").json()["summary"]["best"]

    assert best["price"] == 120.0
    assert outcome["metrics"]["rejected_by_constraints"] == 110


def test_the_optimiser_returns_the_neighbourhood_not_just_the_answer(client, api):
    """A flat optimum has to be visible as one, so the runners-up are returned."""
    model = make(
        client, api, "Ranked prices", "optimizer",
        {
            "objective": "price * demand",
            "variables": {"price": {"min": 10, "max": 200, "step": 1}},
            "derived": {"demand": "max(0, 500 - 3 * price)"},
            "top": 5,
        },
    )
    outcome = run(client, api, model, kind="optimization")
    payload = client.get(f"{api}/results/{outcome['result_id']}/payload").json()["payload"]
    rows = payload["rows"]

    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5]
    assert rows[0]["objective"] >= rows[-1]["objective"]


def test_an_unbounded_search_is_refused_before_it_can_be_saved(client, api):
    """A grid of 10^12 candidates must never become a model somebody can run."""
    errors = rejected(
        client, api, "Far too large", "optimizer",
        {
            "objective": "a * b",
            "variables": {
                "a": {"min": 0, "max": 100000, "step": 0.1},
                "b": {"min": 0, "max": 100000, "step": 0.1},
            },
        },
    )
    assert any("limit" in message for message in errors)


def test_an_objective_reading_an_undeclared_name_is_caught(client, api):
    errors = rejected(
        client, api, "Undeclared objective", "optimizer",
        {"objective": "price * mystery", "variables": {"price": {"min": 1, "max": 2}}},
    )
    assert any("mystery" in message for message in errors)


# --------------------------------------------------------------------------
# simulation: monte carlo
# --------------------------------------------------------------------------
def test_a_simulation_reports_a_distribution_not_a_number(client, api):
    model = make(
        client, api, "Profit at risk", "monte-carlo",
        {
            "expression": "(price - cost) * demand",
            "inputs": {
                "price": {"distribution": "fixed", "value": 50},
                "cost": {"distribution": "normal", "mean": 20, "sd": 3},
                "demand": {
                    "distribution": "triangular",
                    "min": 100,
                    "mode": 400,
                    "max": 900,
                },
            },
            "trials": 5000,
            "thresholds": {"below_10k": {"op": "<", "value": 10000}},
        },
    )
    outcome = run(client, api, model, kind="simulation")
    summary = client.get(f"{api}/results/{outcome['result_id']}").json()["summary"]

    assert summary["trials"] == 5000
    #  mean ≈ (50 − 20) × 466⅔ ≈ 14,000, and the order has to hold.
    assert 11_000 < summary["mean"] < 17_000
    assert summary["p5"] < summary["p50"] < summary["p95"]
    assert summary["min"] <= summary["p5"] and summary["p95"] <= summary["max"]
    assert 0.0 <= summary["probabilities"]["below_10k"] <= 1.0


def test_the_same_seed_gives_the_same_simulation(client, api):
    config = {
        "expression": "a + b",
        "inputs": {
            "a": {"distribution": "normal", "mean": 10, "sd": 2},
            "b": {"distribution": "uniform", "min": 0, "max": 5},
        },
        "trials": 2000,
        "seed": 7,
    }
    first = run(client, api, make(client, api, "Seeded run A", "monte-carlo", config))
    second = run(client, api, make(client, api, "Seeded run B", "monte-carlo", config))
    assert first["metrics"]["mean"] == second["metrics"]["mean"]
    assert first["metrics"]["p95"] == second["metrics"]["p95"]


def test_a_different_seed_gives_a_different_draw(client, api):
    def config(seed):
        return {
            "expression": "a",
            "inputs": {"a": {"distribution": "normal", "mean": 0, "sd": 1}},
            "trials": 2000,
            "seed": seed,
        }

    first = run(client, api, make(client, api, "Seed 1", "monte-carlo", config(1)))
    second = run(client, api, make(client, api, "Seed 2", "monte-carlo", config(2)))
    assert first["metrics"]["mean"] != second["metrics"]["mean"]


def test_the_histogram_covers_every_trial(client, api):
    model = make(
        client, api, "Histogram check", "monte-carlo",
        {
            "expression": "a",
            "inputs": {"a": {"distribution": "uniform", "min": 0, "max": 10}},
            "trials": 3000,
            "bins": 10,
        },
    )
    outcome = run(client, api, model, kind="simulation")
    payload = client.get(f"{api}/results/{outcome['result_id']}/payload").json()["payload"]
    rows = payload["rows"]

    assert len(rows) == 10
    assert sum(row["trials"] for row in rows) == 3000
    assert abs(sum(row["share"] for row in rows) - 1.0) < 1e-6


def test_an_unknown_distribution_is_named_in_the_error(client, api):
    errors = rejected(
        client, api, "Bad distribution", "monte-carlo",
        {"expression": "a", "inputs": {"a": {"distribution": "beta", "mean": 1}}},
    )
    assert any("beta" in message for message in errors)


def test_an_expression_reading_an_undeclared_input_is_caught(client, api):
    errors = rejected(
        client, api, "Undeclared input", "monte-carlo",
        {"expression": "a * b", "inputs": {"a": {"distribution": "fixed", "value": 1}}},
    )
    assert any("'b'" in message for message in errors)


def test_every_execution_kind_has_a_provider():
    """The same rule `ModelType` has had, applied to what a run can be.

    An `ExecutionKind` nothing implements is a promise the API makes and cannot
    keep: it appears in `/execution-kinds`, a caller submits it, and the
    platform answers that no provider supports it. `evaluation` was the value
    most at risk - it is implemented by the typhoon backtest and by nothing
    else, so removing that plugin would quietly empty it.
    """
    from app.modules.model.domain.plugin import ExecutionKind
    from app.modules.model.domain.registry import registry

    covered: dict[str, list[str]] = {}
    for descriptor in registry.descriptors():
        for kind in descriptor.supported_kinds:
            covered.setdefault(kind.value, []).append(descriptor.key)

    empty = [kind.value for kind in ExecutionKind if not covered.get(kind.value)]
    assert not empty, f"execution kinds no provider implements: {empty}"
