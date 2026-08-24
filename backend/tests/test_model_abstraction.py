"""The Model abstraction must stay broader than machine learning.

These tests pin the architectural rules: training is optional, prediction is
only one execution kind, and the core domain never imports an ML framework.
"""

from __future__ import annotations

from pathlib import Path


def test_every_model_type_has_or_may_have_a_provider(client, api):
    body = client.get(f"{api}/model-types").json()
    types = {entry["type"] for entry in body["types"]}
    assert {
        "machine_learning",
        "statistical",
        "mathematical",
        "rule",
        "optimization",
        "simulation",
        "formula",
        "custom",
    } <= types


def test_non_ml_providers_are_registered(client, api):
    body = client.get(f"{api}/model-providers").json()
    providers = {p["key"]: p for p in body["providers"]}
    assert "formula" in providers and providers["formula"]["trainable"] is False
    assert "rule" in providers and providers["rule"]["trainable"] is False
    assert "typhoon-analog" in providers
    assert providers["typhoon-analog"]["model_type"] == "statistical"
    #  Exactly one provider in the default set is trainable - training is a
    #  capability, not a definition of what a model is.
    assert providers["sklearn"]["trainable"] is True


def test_execution_kinds_are_not_limited_to_prediction(client, api):
    kinds = set(client.get(f"{api}/execution-kinds").json()["kinds"])
    assert {
        "training",
        "prediction",
        "simulation",
        "optimization",
        "calculation",
        "evaluation",
        "transformation",
    } == kinds


def test_result_kinds_are_not_limited_to_prediction(client, api):
    kinds = set(client.get(f"{api}/result-kinds").json()["kinds"])
    assert {"scalar", "table", "time_series", "classification", "probability"} <= kinds


def test_training_an_untrainable_model_is_refused(client, api):
    model = client.post(
        f"{api}/models",
        json={
            "name": "Untrainable formula",
            "provider": "formula",
            "configuration": {"expressions": {"y": "x * 2"}},
        },
    ).json()
    response = client.post(
        f"{api}/executions", json={"model_id": model["id"], "kind": "training"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unsupported"
    assert "training" in body["message"]
    assert "training" not in body["details"].get("supported", [])


def test_domain_layers_do_not_import_ml_frameworks():
    """Rule 4: no sklearn/xgboost/torch anywhere in domain or application code."""
    root = Path(__file__).resolve().parents[1] / "app"
    banned = ("sklearn", "xgboost", "torch", "mlflow")
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("plugins/"):
            continue  # framework-specific code belongs here by design
        text = path.read_text(encoding="utf-8")
        for name in banned:
            if f"import {name}" in text or f"from {name}" in text:
                offenders.append(f"{relative}: {name}")
    assert not offenders, f"ML framework imported outside plugins: {offenders}"


def test_core_domain_does_not_import_infrastructure():
    """Domain modules must not reach into SQLAlchemy or FastAPI."""
    root = Path(__file__).resolve().parents[1] / "app" / "modules"
    offenders = []
    for path in root.rglob("domain/*.py"):
        text = path.read_text(encoding="utf-8")
        for name in ("sqlalchemy", "fastapi", "pydantic"):
            if f"import {name}" in text or f"from {name}" in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, f"domain layer depends on infrastructure: {offenders}"


def test_application_layer_depends_on_ports_not_infrastructure():
    """Application services are wired at the composition root, not hard-coded.

    Only `app/api/deps.py` and `app/core/seed.py` may name a concrete
    repository or reader; everything else works through domain ports.
    """
    root = Path(__file__).resolve().parents[1] / "app" / "modules"
    offenders = []
    for path in root.rglob("application/*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("infrastructure", "sqlalchemy", "from fastapi"):
            if needle in text:
                offenders.append(f"{path.parent.parent.name}/{path.name}: {needle}")
    assert not offenders, f"application layer reaches into infrastructure: {offenders}"


def test_every_module_exposes_its_ports():
    """Each module states the persistence contract its services rely on."""
    root = Path(__file__).resolve().parents[1] / "app" / "modules"
    modules = [d for d in root.iterdir() if d.is_dir() and (d / "application").exists()]
    missing = [
        d.name
        for d in modules
        if (d / "application" / "services.py").exists()
        and not (d / "domain" / "ports.py").exists()
    ]
    assert not missing, f"modules without a ports definition: {missing}"


def test_repositories_satisfy_their_ports():
    """The SQL repositories structurally implement the Protocols they back."""
    from app.modules.applications.domain.ports import ApplicationRepository
    from app.modules.applications.infrastructure.repositories import (
        SqlApplicationRepository,
    )
    from app.modules.data.domain.ports import DatasetRepository, SourceRepository
    from app.modules.data.infrastructure.repositories import (
        SqlDatasetRepository,
        SqlSourceRepository,
    )
    from app.modules.execution.domain.ports import ExecutionRepository
    from app.modules.execution.infrastructure.repositories import SqlExecutionRepository
    from app.modules.model.domain.ports import ModelRepository
    from app.modules.model.infrastructure.repositories import SqlModelRepository
    from app.modules.results.domain.ports import ResultRepository
    from app.modules.results.infrastructure.repositories import SqlResultRepository

    pairs = [
        (SqlSourceRepository, SourceRepository),
        (SqlDatasetRepository, DatasetRepository),
        (SqlModelRepository, ModelRepository),
        (SqlExecutionRepository, ExecutionRepository),
        (SqlResultRepository, ResultRepository),
        (SqlApplicationRepository, ApplicationRepository),
    ]
    for implementation, port in pairs:
        missing = [
            name
            for name in dir(port)
            if not name.startswith("_") and not hasattr(implementation, name)
        ]
        assert not missing, f"{implementation.__name__} is missing {missing}"


def test_routes_stay_thin():
    """Rule: business logic never lives in a route.

    Enforced by length, because that is the shape the rule actually takes: a
    handler that parses a request, calls one service and returns the answer is
    short. `experiment_leaderboard` and `compare_experiments` were 80+ lines
    each and held the comparison semantics of an Experiment - nothing outside
    HTTP could reuse them and nothing could test them directly.

    Raising this ceiling is not the fix when it fails; moving the logic into a
    service is.
    """
    import ast

    root = Path(__file__).resolve().parents[1] / "app"
    ceiling = 40
    offenders = []
    for path in sorted(root.rglob("api/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorated = any(
                "router" in ast.unparse(d) for d in node.decorator_list
            )
            if not decorated:
                continue
            length = (node.end_lineno or node.lineno) - node.lineno
            if length > ceiling:
                offenders.append(
                    f"{path.relative_to(root).as_posix()}::{node.name} "
                    f"({length} lines)"
                )
    assert not offenders, (
        "route handlers are carrying logic that belongs in a service: "
        f"{offenders}"
    )
