"""Idempotent seeding of the golden path, plus whatever the plugins bring.

Running this on an already-seeded database is a no-op. It exists so a fresh
checkout starts with something real to look at:

    sales.csv -> Dataset -> Formula model -> Execution -> Result -> Chart

Built-in applications seed themselves: this file knows that plugins have
things to set up, and nothing about what any of them are.

The services come from the container, so seeding uses whatever storage backend
and execution mode the deployment is configured with.
"""

from __future__ import annotations

import csv
import logging
import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.container import Services, build_services
from app.modules.execution.domain.ports import RunInline
from app.modules.model.domain.registry import registry
from app.modules.platform.application.workspaces import WorkspaceService
from app.modules.platform.domain.projects import (
    DEFAULT_PROJECT_NAME,
    SOURCES_SUBDIRECTORY,
)
from app.modules.platform.infrastructure.workspace_repositories import (
    SqlWorkspaceRepository,
)
from app.shared.errors import FluxError
from app.shared.scoping import WorkspaceScope

logger = logging.getLogger(__name__)

SALES_CSV_RELATIVE = f"{DEFAULT_PROJECT_NAME}/{SOURCES_SUBDIRECTORY}/sales.csv"
SALES_DATASET = "Sales"
REVENUE_MODEL = "Revenue formula"
RISK_MODEL = "Sales risk rules"
TREND_MODEL = "Revenue trend"
FORECAST_MODEL = "Revenue forecast"
PRICE_OPTIMISER_MODEL = "Best price"
PROFIT_SIMULATION_MODEL = "Profit at risk"


def seed_all(session: Session) -> None:
    settings = get_settings()

    #  Seed into the default workspace rather than into no workspace at all.
    #  A row with no workspace is invisible to every scoped query, which is
    #  every query the API makes - the seeded example would exist and never be
    #  found again.
    workspaces = WorkspaceService(SqlWorkspaceRepository(session))
    default = workspaces.default()
    session.flush()
    scope = WorkspaceScope(workspace_id=default.id)

    #  The golden path is sample material, so it is filed in the workspace's
    #  default project — which this creates, along with its directory. Named
    #  generically on purpose: a project named after a piece of work is
    #  declared by the plugin that does that work, never here.
    project = build_services(session, settings=settings, scope=scope).projects.default()
    session.flush()
    services = build_services(
        session, settings=settings, scope=scope.within(project.id)
    )

    #  Each step gets its own savepoint: one optional sample failing to load
    #  must not roll back everything seeded before it.
    sales_dataset = _step(session, "sales dataset",
                          lambda: _seed_sales(settings.data_root, services))
    seeded_models = _step(session, "models", lambda: _seed_models(services)) or {}
    _step(
        session,
        "golden path",
        lambda: _seed_golden_path(sales_dataset, seeded_models, services),
    )
    _step(
        session,
        "applications",
        lambda: _seed_applications(services, seeded_models, sales_dataset),
    )
    #  The domain worked example: analysis, validation, scheduling and a report,
    #  Whatever the plugins bring, set up the same way as anything else. This
    #  file does not know which applications exist, which is what makes adding
    #  one a matter of adding a plugin.
    _seed_contributions(session, services)


def _step(session: Session, label: str, action):
    """Run one seeding step inside a savepoint, logging and skipping failures."""
    savepoint = session.begin_nested()
    try:
        outcome = action()
        savepoint.commit()
        return outcome
    except Exception as exc:
        savepoint.rollback()
        logger.warning("skipped seeding %s: %s", label, exc)
        return None


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def _seed_sales(data_root: Path, services: Services):
    existing = services.datasets.datasets.get_by_name(SALES_DATASET)
    if existing:
        return existing

    csv_path = data_root / SALES_CSV_RELATIVE
    if not csv_path.exists():
        _write_sales_csv(csv_path)

    sources = services.sources
    source = sources.repository.get_by_name("Sales sample (CSV)") or sources.create(
        name="Sales sample (CSV)",
        source_type="csv",
        connection={"path": SALES_CSV_RELATIVE},
        description="Bundled sample used by the golden-path walkthrough",
    )
    dataset, _ = services.datasets.create_from_source(
        source_id=source.id,
        name=SALES_DATASET,
        description="Daily product sales: date, product, price, quantity",
        tags=["sample"],
    )
    logger.info("seeded dataset '%s'", dataset.name)
    return dataset


def _write_sales_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260819)
    products = [("Widget", 24.5), ("Gadget", 61.0), ("Doohickey", 12.75)]
    start = date(2026, 1, 1)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "product", "price", "quantity"])
        for day in range(90):
            current = start + timedelta(days=day)
            for name, base_price in products:
                price = round(base_price * rng.uniform(0.9, 1.1), 2)
                quantity = rng.randint(3, 40)
                writer.writerow([current.isoformat(), name, price, quantity])



def _seed_models(services: Services) -> dict[str, object]:
    definitions = [
        {
            "name": REVENUE_MODEL,
            "provider": "formula",
            "description": "revenue = price x quantity",
            "configuration": {
                "expressions": {"revenue": "price * quantity"},
                "keep_input_columns": True,
            },
            "tags": ["sample", "golden-path"],
        },
        {
            "name": RISK_MODEL,
            "provider": "rule",
            "description": "Flags unusually large or small orders",
            "configuration": {
                "rules": [
                    {"name": "bulk order", "when": "quantity >= 30",
                     "then": {"order_class": "BULK"}},
                    {"name": "premium price", "when": "price > 60",
                     "then": {"order_class": "PREMIUM"}},
                ],
                "default": {"order_class": "STANDARD"},
                "mode": "first_match",
            },
            "tags": ["sample"],
        },
        {
            "name": TREND_MODEL,
            "provider": "python-transform",
            "description": "7-point moving average of quantity",
            "configuration": {
                "transform": "moving_average",
                "options": {"column": "quantity", "window": 7},
            },
            "tags": ["sample"],
        },
        {
            "name": PRICE_OPTIMISER_MODEL,
            "provider": "optimizer",
            "description": (
                "Revenue peaks where price and the demand it suppresses balance. "
                "No training, no data: an objective, a bounded variable and the "
                "grid the platform searches."
            ),
            "configuration": {
                "objective": "price * demand",
                "goal": "maximise",
                "variables": {"price": {"min": 10, "max": 200, "step": 1}},
                "derived": {"demand": "max(0, 500 - 3 * price)"},
                "top": 10,
            },
            "tags": ["sample", "optimization"],
        },
        {
            "name": PROFIT_SIMULATION_MODEL,
            "provider": "monte-carlo",
            "description": (
                "The same profit calculation with the uncertainty left in: cost "
                "and demand are distributions, so the answer is a range and a "
                "probability rather than a single reassuring number."
            ),
            "configuration": {
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
                "trials": 20000,
                "seed": 42,
                "thresholds": {"below_10k": {"op": "<", "value": 10000}},
            },
            "tags": ["sample", "simulation"],
        },
        {
            "name": FORECAST_MODEL,
            "provider": "sklearn",
            "description": (
                "Learns revenue from price and quantity. It reaches the same "
                "answer as the formula model, but needs a training execution "
                "first - which is exactly why training is optional, not intrinsic."
            ),
            "configuration": {
                "algorithm": "random_forest_regressor",
                "target": "revenue",
                "features": ["price", "quantity"],
                "test_size": 0.25,
            },
            "tags": ["sample", "ml"],
        },
    ]

    seeded: dict[str, object] = {}
    for spec in definitions:
        existing = services.models.repository.get_by_name(spec["name"])
        if existing:
            seeded[spec["name"]] = existing
            continue
        if not registry.has(spec["provider"]):
            logger.warning("provider '%s' is not registered; skipping", spec["provider"])
            continue
        try:
            seeded[spec["name"]] = services.models.create(**spec)
            logger.info("seeded model '%s'", spec["name"])
        except FluxError as exc:
            logger.warning("could not seed model '%s': %s", spec["name"], exc)
    return seeded


# --------------------------------------------------------------------------
# golden path: execute, then chart the result
# --------------------------------------------------------------------------
def _seed_golden_path(sales_dataset, models: dict, services: Services) -> None:
    """Run the walkthrough end to end so a fresh install has something to see.

    Seeding runs its executions in-process even when the deployment is in queue
    mode: the point of the golden path is that the whole chain exists by the
    time the API is up, and a worker may not have started yet.
    """
    revenue_model = models.get(REVENUE_MODEL)
    if not sales_dataset or not revenue_model:
        return
    if any(v.name == "Revenue by product" for v in services.visualizations.list()):
        return

    services.executions.dispatcher = RunInline()

    try:
        execution = services.executions.submit(
            model_id=revenue_model.id,
            kind="calculation",
            dataset_id=sales_dataset.id,
        )
    except FluxError as exc:
        logger.warning("golden-path execution failed: %s", exc)
        return

    result = services.results.for_execution(execution.id)
    if not result or not result.dataset_version_id:
        return

    #  Train the ML model on the same result dataset, so the library ships with
    #  one trained Model Version and the contrast with the formula is visible.
    forecast = models.get(FORECAST_MODEL)
    if forecast:
        try:
            services.executions.submit(
                model_id=forecast.id,
                kind="training",
                dataset_version_id=result.dataset_version_id,
            )
        except FluxError as exc:
            logger.warning("could not train the sample forecast model: %s", exc)

    chart = services.visualizations.create(
        name="Revenue by product",
        description="Total revenue per product, computed by the formula model",
        dataset_version_id=result.dataset_version_id,
        spec={
            "chart_type": "bar",
            "x": "product",
            "y": ["revenue"],
            "aggregation": "sum",
        },
    )
    trend = services.visualizations.create(
        name="Revenue over time",
        description="Daily revenue from the same result dataset",
        dataset_version_id=result.dataset_version_id,
        spec={
            "chart_type": "line",
            "x": "date",
            "y": ["revenue"],
            "aggregation": "sum",
            "sort_by": "date",
        },
    )
    services.dashboards.create(
        name="Sales overview",
        description="The golden path end to end: data, model, execution, result, chart",
        tiles=[
            {"visualization_id": chart.id, "x": 0, "y": 0, "width": 6, "height": 4},
            {"visualization_id": trend.id, "x": 6, "y": 0, "width": 6, "height": 4},
        ],
    )
    logger.info("seeded the golden-path dashboard")


# --------------------------------------------------------------------------
# applications
# --------------------------------------------------------------------------
def _seed_applications(services: Services, models: dict, sales_dataset) -> None:
    by_name = {a.name: a for a in services.applications.list()}
    existing = set(by_name)
    dashboards = {d.name: d.id for d in services.dashboards.list()}

    def dashboard_ids(*names: str) -> list[str]:
        """The dashboards this application shows, by name.

        By name because the seeder does not own the dashboards - the
        climatology half creates them - and a missing one should leave the
        application slightly emptier rather than fail the whole seed.
        """
        return [dashboards[name] for name in names if name in dashboards]


    if "Sales analytics" not in existing and sales_dataset:
        sales_app = services.applications.create(
            name="Sales analytics",
            description="The bundled walkthrough: formula, rules, transform and ML.",
            model_ids=[
                m.id
                for m in (
                    models.get(REVENUE_MODEL),
                    models.get(RISK_MODEL),
                    models.get(TREND_MODEL),
                    models.get(FORECAST_MODEL),
                )
                if m is not None
            ],
            dataset_ids=[sales_dataset.id],
            dashboard_ids=dashboard_ids("Sales overview"),
            entrypoint="/dashboards",
        )
        #  The walkthrough is meant to be opened, and an application is only
        #  offered once it is published. Left as a draft, a fresh install and a
        #  migrated one disagreed about the same shipped example.
        services.applications.publish(sales_app.id)
    elif by_name.get("Sales analytics") and not by_name["Sales analytics"].dashboard_ids:
        #  Seeded by an earlier version, which bundled no dashboards at all.
        services.applications.update(
            by_name["Sales analytics"].id,
            {"dashboard_ids": dashboard_ids("Sales overview")},
        )


def _seed_contributions(session: Session, services: Services) -> None:
    """Set up whatever the plugins declare, in the order they declare it.

    Each plugin gets a savepoint of its own. Without one, a failure anywhere
    in the last plugin rolled back every plugin before it — one application
    would seed completely, the next would hit a bad join, and the database
    would come up with neither. The rule this file already states is that one
    application failing costs that application; a shared savepoint made that
    rule untrue.

    The exception clause is `Exception` rather than `FluxError` for the same
    reason. A plugin's failure arrives in whatever form its libraries raise —
    the one that motivated this was Arrow refusing a column name — and a
    seeder that only survives the platform's own error type does not survive
    the ones that actually happen.
    """
    from app.plugins.contrib import contributed_seeders
    from app.plugins.fixtures import FixtureLoader

    for contributed in contributed_seeders():
        _step(
            session,
            f"plugin '{contributed.source}'",
            lambda contributed=contributed: _seed_one_plugin(
                FixtureLoader, session, services, contributed
            ),
        )


def _seed_one_plugin(loader_class, session, services: Services, contributed) -> None:
    #  Everything this plugin creates is filed under the project it declares.
    #  Done by rebuilding the services inside that project rather than by
    #  passing an id through every section: filing then happens in the
    #  repository, where it happens for everything else, and a section added
    #  later cannot forget to do it.
    declared = getattr(contributed.fixture, "project", None)
    if declared:
        existing = services.projects.repository.get_by_name(declared["name"])
        project = existing or services.projects.create(**declared)
        services.projects.ensure_directory(project)
        session.flush()
        services = build_services(
            session,
            settings=services.settings,
            scope=WorkspaceScope(
                workspace_id=services.projects.repository.scope.workspace_id,
                user_id=services.projects.repository.scope.user_id,
                project_id=project.id,
            ),
        )
    loader = loader_class(services) if contributed.fixture is not None else None
    if loader is not None:
        #  Not the final pass when a code seeder follows: what it builds
        #  cannot be referred to yet.
        loader.load(contributed.fixture, final=contributed.seed is None)
    if contributed.seed is not None:
        contributed.seed(services)
    if loader is not None:
        #  The code half builds things the declarative half wants to bundle
        #  - the dashboards an application shows exist only once its charts
        #  have been computed - so the fixture gets a second pass to pick
        #  them up. It is idempotent by construction, which makes the
        #  second pass lookups plus the parts that were missing.
        loader.load(contributed.fixture)
