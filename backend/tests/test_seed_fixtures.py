"""Seeding a built-in application from a declaration rather than a script.

The loader makes three promises, and each is worth holding it to: that names
are enough to wire resources to each other, that running it twice leaves one of
everything, and that one broken section costs that section rather than the
whole application.

The fourth check is about the platform rather than the loader: `app/core/`
must not know that a typhoon application exists.
"""

from __future__ import annotations

import pytest

from app.plugins.fixtures import Fixture, FixtureLoader


def _fixture(name: str) -> Fixture:
    """A small but complete declaration: every section, wired by name."""
    return Fixture(
        source="test",
        sources=[
            {
                "name": f"{name} rows",
                "source_type": "inline",
                "connection": {
                    "rows": [{"city": "Taipei", "n": 3}, {"city": "Tainan", "n": 5}]
                },
            }
        ],
        datasets=[{"name": f"{name} data", "source": f"{name} rows"}],
        models=[
            {
                "name": f"{name} model",
                "provider": "formula",
                "configuration": {"expressions": {"doubled": "n * 2"}},
            }
        ],
        visualizations=[
            {
                "name": f"{name} chart",
                "dataset": f"{name} data",
                "spec": {"chart_type": "bar", "x": "city", "y": ["n"]},
            }
        ],
        dashboards=[
            {
                "name": f"{name} board",
                "tiles": [{"visualization": f"{name} chart", "width": 6}],
            }
        ],
        applications=[
            {
                "name": f"{name} app",
                "models": [f"{name} model"],
                "datasets": [f"{name} data"],
                "dashboards": [f"{name} board"],
                "entrypoint": "/dashboards",
                "publish": True,
            }
        ],
    )


@pytest.fixture
def services(app):
    #  Depends on `app` for the schema and the registered plugins, not for the
    #  HTTP surface: a fixture is loaded through the services, as the seed does.
    from app.core.container import build_services
    from app.core.database import session_scope

    with session_scope() as session:
        yield build_services(session)


def test_a_declaration_is_enough_to_wire_everything(services):
    """Names, not ids: the declaration cannot know ids that do not exist yet."""
    FixtureLoader(services).load(_fixture("wiring"))

    application = next(a for a in services.applications.list() if a.name == "wiring app")
    dashboard = next(d for d in services.dashboards.list() if d.name == "wiring board")
    visualization = next(
        v for v in services.visualizations.list() if v.name == "wiring chart"
    )

    assert application.dashboard_ids == [dashboard.id]
    assert len(application.model_ids) == 1 and len(application.dataset_ids) == 1
    #  The tile refers to the chart the fixture named, translated to its id.
    assert [t.visualization_id for t in dashboard.tiles] == [visualization.id]
    assert application.status.value == "published"


def test_seeding_twice_leaves_one_of_everything(services):
    """Idempotent by construction, because every install re-runs the seed."""
    fixture = _fixture("twice")
    FixtureLoader(services).load(fixture)
    FixtureLoader(services).load(fixture)

    def count(items, name: str) -> int:
        return sum(1 for item in items if item.name == name)

    assert count(services.applications.list(), "twice app") == 1
    assert count(services.dashboards.list(), "twice board") == 1
    assert count(services.visualizations.list(), "twice chart") == 1
    assert count(services.models.repository.list(), "twice model") == 1


def test_a_second_pass_fills_in_what_was_missing(services):
    """The code half builds dashboards the declarative half wants to bundle.

    On a first run the dashboard does not exist yet, so the application is
    created without it; the pass that follows the code seeder must repair that
    rather than leave a permanently half-built application.
    """
    fixture = _fixture("later")
    without_dashboard = Fixture(**{**fixture.__dict__, "dashboards": []})
    FixtureLoader(services).load(without_dashboard)

    application = next(a for a in services.applications.list() if a.name == "later app")
    assert application.dashboard_ids == []

    FixtureLoader(services).load(fixture)
    application = next(a for a in services.applications.list() if a.name == "later app")
    assert len(application.dashboard_ids) == 1


def test_one_broken_section_does_not_cost_the_others(services):
    """A missing data file should cost that dataset, not the application."""
    fixture = _fixture("broken")
    fixture.sources[0]["connection"] = {"rows": "not rows at all"}

    FixtureLoader(services).load(fixture)

    names = {a.name for a in services.applications.list()}
    assert "broken app" in names, "the application was lost with its dataset"
    #  And it is honestly empty rather than pointing at things that failed.
    application = next(a for a in services.applications.list() if a.name == "broken app")
    assert application.dataset_ids == []
    assert len(application.model_ids) == 1


def test_the_core_does_not_know_the_typhoon_application_exists():
    """Adding or removing a built-in application must not edit `app/core/`."""
    from pathlib import Path

    core = Path(__file__).resolve().parents[1] / "app" / "core"
    offenders = [
        path.name
        for path in core.glob("*.py")
        if "typhoon" in path.read_text(encoding="utf-8").lower()
    ]
    assert not offenders, f"core still names a domain application: {offenders}"


def test_the_typhoon_plugin_declares_its_own_resources():
    """What used to be a hundred lines of service calls in the core seeder."""
    from app.plugins.contrib import contributed_seeders

    seeders = {c.source: c for c in contributed_seeders()}
    typhoon = seeders["typhoon_analog"]

    assert typhoon.fixture is not None and typhoon.seed is not None
    declared = {model["name"] for model in typhoon.fixture.models}
    assert {"Typhoon analog", "Wind against pressure"} <= declared
    #  Every cross-reference resolves to something the same fixture declares.
    datasets = {dataset["name"] for dataset in typhoon.fixture.datasets}
    sources = {source["name"] for source in typhoon.fixture.sources}
    assert all(dataset["source"] in sources for dataset in typhoon.fixture.datasets)
    for application in typhoon.fixture.applications:
        assert set(application.get("models", [])) <= declared
        assert set(application.get("datasets", [])) <= datasets


def test_a_fixture_can_bundle_what_code_built(services):
    """Not everything a fixture refers to is declared by a fixture.

    The typhoon dashboards are computed by the plugin's own seeder, and the
    application still has to bundle them. Resolving names only against what
    this run created is how an application ends up published and empty.
    """
    dashboard = services.dashboards.create(
        name="built in code", tiles=[]
    )
    fixture = Fixture(
        source="test",
        applications=[
            {
                "name": "bundling app",
                "dashboards": ["built in code"],
                "entrypoint": "/dashboards",
            }
        ],
    )
    FixtureLoader(services).load(fixture)

    application = next(
        a for a in services.applications.list() if a.name == "bundling app"
    )
    assert application.dashboard_ids == [dashboard.id]


def test_the_typhoon_application_bundles_dashboards_that_exist():
    """A name typed twice is a name that drifts.

    The application was published referring to a dashboard called "Typhoon
    climatology", which the climatology seeder has never created - so a fresh
    install got an application with nothing in it, and nothing said so.
    """
    from app.plugins.typhoon_analog.seed.climatology import DASHBOARDS
    from app.plugins.typhoon_analog.seed.resources import fixture

    built = {board["name"] for board in DASHBOARDS}
    for application in fixture().applications:
        bundled = set(application.get("dashboards", []))
        assert bundled, f"'{application['name']}' would open on nothing"
        assert bundled <= built, f"names nothing creates: {sorted(bundled - built)}"


def test_the_first_pass_does_not_complain_about_what_comes_later(services, caplog):
    """A reference to something the code seeder has not built yet is normal.

    Warning about it on the first pass trains the reader to ignore the message
    that matters on the second - which is exactly how an application published
    with a dashboard name nothing creates went unnoticed for a release.
    """
    fixture = Fixture(
        source="test",
        applications=[
            {
                "name": "quiet first pass",
                "dashboards": ["built later"],
                "entrypoint": "/dashboards",
            }
        ],
    )

    with caplog.at_level("WARNING"):
        FixtureLoader(services).load(fixture, final=False)
    assert "built later" not in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING"):
        FixtureLoader(services).load(fixture)
    assert "built later" in caplog.text
