"""What plugins contribute to the platform: routes, and things to seed.

A provider that ships a user-facing application needs endpoints of its own —
the typhoon forecast shapes a map request into an ordinary Execution. Without
somewhere to declare that, the platform core ends up importing a router by name
and a general platform acquires a hard dependency on one domain.

A plugin registers here; the core mounts whatever it finds. Adding or removing
an application touches this file and the plugin, never the router.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter

from app.api.security import ModuleGuard


@dataclass(frozen=True)
class ContributedRouter:
    """A plugin's routes, plus the permissions they are mounted behind."""

    router: APIRouter
    guard: ModuleGuard
    #  Named so a reader of the mounted API can tell where a path came from.
    source: str


def contributed_routers() -> list[ContributedRouter]:
    """Every plugin-supplied router, in mount order.

    Imported lazily so the core module graph stays free of plugin imports.
    """
    from app.api.security import BUILTIN_APP_GUARD
    from app.plugins.typhoon_analog.routes import router as typhoon_router

    return [
        ContributedRouter(typhoon_router, BUILTIN_APP_GUARD, "typhoon_analog"),
    ]


@dataclass(frozen=True)
class ContributedSeeder:
    """A plugin's first-run setup.

    `fixture` is the declarative half - the resources that should exist - and
    `seed` is the part that genuinely needs code: running a backtest, recording
    an evaluation, building a chart from a result. A plugin may supply either,
    both, or neither.
    """

    source: str
    fixture: object | None = None
    seed: object | None = None


def contributed_seeders() -> list[ContributedSeeder]:
    """Every plugin's first-run setup, in the order it should run.

    Imported lazily, and by iteration rather than by name, so that adding a
    built-in application means adding a plugin and a line here - never editing
    anything under `app/core/`.
    """
    from app.plugins.typhoon_analog.seed import seed_typhoon_example
    from app.plugins.typhoon_analog.seed.resources import fixture as typhoon_fixture

    return [
        ContributedSeeder(
            source="typhoon_analog",
            fixture=typhoon_fixture(),
            seed=seed_typhoon_example,
        ),
    ]
