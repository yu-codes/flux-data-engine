"""Declaring the resources a built-in application ships with.

A plugin that wants a dataset, a few models and a dashboard in place on first
run should not have to write a hundred lines of service calls to get them. Most
of what a seeder does is not logic at all - it is a list of things that should
exist - and a list is better written as data.

What stays in code is the part that genuinely is code: running a backtest,
recording an evaluation, computing a chart from a result. Those are actions
with outcomes, and pretending they are data would mean inventing a language to
describe them in.

A fixture is idempotent by construction: everything is looked up by name first,
so seeding twice leaves one of each rather than two.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.shared.errors import FluxError

logger = logging.getLogger(__name__)


@dataclass
class Fixture:
    """The resources one built-in application declares.

    Ordered by dependency, because a dashboard needs its visualisations and a
    visualisation needs its dataset: the loader walks these in the order they
    are declared here, and each section may refer to names from the ones above
    it.
    """

    #  Named so a log line can say which plugin asked for what.
    source: str
    #  The project everything below is filed under: `{"name": ..., ...}`.
    #  Declared by the plugin rather than by the core, because a project is
    #  named after a piece of work and the core of a general platform must not
    #  know that any particular piece of work exists.
    #
    #  Everything in the sections below is created *inside* it — not by each
    #  section passing an id around, but by the seeder building its services
    #  scoped to the project first. Filing then happens where filing always
    #  happens, in the repository, and no section has to remember.
    project: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    models: list[dict[str, Any]] = field(default_factory=list)
    pipelines: list[dict[str, Any]] = field(default_factory=list)
    visualizations: list[dict[str, Any]] = field(default_factory=list)
    dashboards: list[dict[str, Any]] = field(default_factory=list)
    applications: list[dict[str, Any]] = field(default_factory=list)


class FixtureLoader:
    """Create what a fixture declares, once.

    Every resource is found by name before being created, so a second run is a
    series of lookups rather than a series of conflicts. Names are also how
    sections refer to each other - a dashboard names its visualisations, a
    visualisation names its dataset - because ids do not exist until the thing
    does.
    """

    def __init__(self, services):
        self.services = services
        #  Whether unresolved references are worth complaining about yet.
        self.final = True
        #  Name -> id, per kind, built as we go.
        self.made: dict[str, dict[str, str]] = {
            "sources": {},
            "datasets": {},
            "models": {},
            "visualizations": {},
            "dashboards": {},
        }

    def load(self, fixture: Fixture, *, final: bool = True) -> None:
        """Create what the fixture declares.

        `final` says whether this is the last pass. A fixture may be loaded
        twice - once before the plugin's code seeder and once after - and on
        the first pass a reference to something that seeder has not built yet
        is expected, not a problem. Warning about it there would train the
        reader to ignore exactly the message that matters on the second.
        """
        self.final = final
        for step in (
            self._sources,
            self._datasets,
            self._models,
            self._visualizations,
            self._dashboards,
            self._applications,
        ):
            try:
                step(fixture)
            except FluxError as exc:
                #  One section failing must not lose the ones before it: a
                #  missing optional data file should cost that dataset, not the
                #  whole application.
                logger.warning(
                    "fixture '%s': %s could not be loaded: %s",
                    fixture.source,
                    step.__name__.lstrip("_"),
                    exc,
                )

    def _find(self, kind: str, name: str) -> str | None:
        """The id of something this fixture names, whoever created it.

        Not everything a fixture refers to is declared by a fixture: the charts
        and dashboards a plugin computes are built in code, and an application
        still has to be able to bundle them. So a name is resolved against what
        this run made first, and against what is already there second.
        """
        made = self.made[kind].get(name)
        if made is not None:
            return made
        found = self._existing(kind, name)
        if found is not None:
            self.made[kind][name] = found
        return found

    def _existing(self, kind: str, name: str) -> str | None:
        found = {
            "sources": lambda: self.services.sources.repository.get_by_name(name),
            "datasets": lambda: self.services.datasets.datasets.get_by_name(name),
            "models": lambda: self.services.models.repository.get_by_name(name),
            "dashboards": lambda: self.services.dashboards.repository.get_by_name(name),
            "visualizations": lambda: next(
                (v for v in self.services.visualizations.list() if v.name == name), None
            ),
        }[kind]()
        return found.id if found is not None else None

    def _ref(self, kind: str, name: str, wanted_by: str) -> str | None:
        """A reference that must resolve, or a warning saying it did not.

        A section that failed leaves its names unresolved, and what depends on
        them should be skipped rather than crash: a broken data file costs the
        dataset and its charts, not the application they belong to.
        """
        found = self._find(kind, name)
        if found is None and self.final:
            logger.warning(
                "'%s' skipped: it needs %s '%s', which does not exist",
                wanted_by,
                kind[:-1],
                name,
            )
        return found

    def _refs(self, kind: str, names: list[str], wanted_by: str) -> list[str]:
        """The references that resolve, with a warning for the ones that do not.

        Dropping them silently is how an application ends up published and
        empty: it named a dashboard nothing creates, and the only evidence was
        a page with nothing on it.
        """
        found = (self._ref(kind, name, wanted_by) for name in names)
        return [item for item in found if item is not None]

    # -- sections ----------------------------------------------------------
    def _sources(self, fixture: Fixture) -> None:
        for spec in fixture.sources:
            existing = self.services.sources.repository.get_by_name(spec["name"])
            source = existing or self.services.sources.create(**spec)
            self.made["sources"][spec["name"]] = source.id

    def _datasets(self, fixture: Fixture) -> None:
        for spec in fixture.datasets:
            existing = self.services.datasets.datasets.get_by_name(spec["name"])
            if existing:
                self.made["datasets"][spec["name"]] = existing.id
                continue
            payload = dict(spec)
            source_id = self._ref("sources", payload.pop("source"), spec["name"])
            if source_id is None:
                continue
            payload["source_id"] = source_id
            dataset, _ = self.services.datasets.create_from_source(**payload)
            self.made["datasets"][spec["name"]] = dataset.id

    def _models(self, fixture: Fixture) -> None:
        for spec in fixture.models:
            existing = self.services.models.repository.get_by_name(spec["name"])
            model = existing or self.services.models.create(**spec)
            self.made["models"][spec["name"]] = model.id

    def _visualizations(self, fixture: Fixture) -> None:
        for spec in fixture.visualizations:
            existing = next(
                (v for v in self.services.visualizations.list() if v.name == spec["name"]),
                None,
            )
            if existing:
                self.made["visualizations"][spec["name"]] = existing.id
                continue
            payload = dict(spec)
            dataset_id = self._ref("datasets", payload.pop("dataset"), spec["name"])
            if dataset_id is None:
                continue
            payload["dataset_id"] = dataset_id
            visualization = self.services.visualizations.create(**payload)
            self.made["visualizations"][spec["name"]] = visualization.id

    def _dashboards(self, fixture: Fixture) -> None:
        for spec in fixture.dashboards:
            existing = next(
                (d for d in self.services.dashboards.list() if d.name == spec["name"]),
                None,
            )
            if existing:
                self.made["dashboards"][spec["name"]] = existing.id
                continue
            payload = dict(spec)
            tiles = payload.pop("tiles", [])
            payload["tiles"] = [
                {**tile, "visualization_id": viz_id}
                for tile, viz_id in (
                    (tile, self._find("visualizations", tile.pop("visualization", "")))
                    for tile in (dict(t) for t in tiles)
                )
                if viz_id is not None
            ]
            dashboard = self.services.dashboards.create(**payload)
            self.made["dashboards"][spec["name"]] = dashboard.id

    def _applications(self, fixture: Fixture) -> None:
        for spec in fixture.applications:
            existing = next(
                (a for a in self.services.applications.list() if a.name == spec["name"]),
                None,
            )
            payload = dict(spec)
            publish = payload.pop("publish", False)
            name = spec["name"]
            payload["model_ids"] = self._refs("models", payload.pop("models", []), name)
            payload["dataset_ids"] = self._refs(
                "datasets", payload.pop("datasets", []), name
            )
            payload["dashboard_ids"] = self._refs(
                "dashboards", payload.pop("dashboards", []), name
            )
            if existing:
                #  An install seeded by an earlier version may be missing the
                #  parts added since; fill them in rather than leaving a
                #  half-built application nobody can repair from the UI.
                self.services.applications.update(existing.id, payload)
                application = existing
            else:
                application = self.services.applications.create(**payload)
            if publish and application.status.value != "published":
                self.services.applications.publish(application.id)
