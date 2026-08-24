"""What the typhoon application declares, as data.

Everything here used to live in `app/core/seed.py`, which meant the core of a
general platform named a dataset, two models and an application belonging to
one domain - and the project's own rule says adding or removing a built-in
application must never edit a file under `app/core/`.

Written as a fixture rather than as service calls because none of it is logic.
It is a list of things that should exist, and a list reads better as a list.
The parts that genuinely *are* logic - running the backtests, recording the
evaluations, building the charts from a run - stay as code in this package,
where they belong.
"""

from __future__ import annotations

from app.plugins.fixtures import Fixture

from ..paths import SUBDIRECTORY
from .climatology import DASHBOARDS

DATASET = "Taiwan typhoon catalogue"
ANALOG_MODEL = "Typhoon analog"
PRECIP_MODEL = "Typhoon precipitation probability"
WIND_PRESSURE_MODEL = "Wind against pressure"
APPLICATION = "Typhoon analog forecast"

#  Relative to the platform's data root, which is the only thing this plugin
#  needs from the platform's configuration.
OVERVIEW_PATH = "/".join((*SUBDIRECTORY, "typhoons_overview.json"))


def fixture() -> Fixture:
    """The resources this application ships with."""
    return Fixture(
        source="typhoon_analog",
        sources=[
            {
                "name": "Taiwan typhoon overview",
                "source_type": "json",
                #  The overview file is a top-level array of typhoon records.
                "connection": {"path": OVERVIEW_PATH},
                "description": "CWA typhoon records joined with IBTrACS tracks",
            }
        ],
        datasets=[
            {
                "name": DATASET,
                "source": "Taiwan typhoon overview",
                "description": (
                    "Historical typhoons affecting Taiwan, with track categories"
                ),
                "tags": ["typhoon", "builtin"],
            }
        ],
        models=[
            {
                "name": ANALOG_MODEL,
                "provider": "typhoon-analog",
                "description": (
                    "Coastline-RRF analog forecasting of the CWA landfall-track "
                    "category from a drawn or chosen track."
                ),
                "configuration": {"method": "coastline_rrf", "buffer_km": 500.0},
                "tags": ["typhoon", "builtin"],
            },
            {
                "name": PRECIP_MODEL,
                "provider": "typhoon-precip-analog",
                "description": (
                    "Position-conditioned precipitation probability over Taiwan"
                ),
                "configuration": {},
                "tags": ["typhoon", "builtin"],
            },
            {
                #  Demonstrates the curve-fit provider on real measurements
                #  rather than on invented ones, which is why it ships with the
                #  data it reads rather than with the platform.
                "name": WIND_PRESSURE_MODEL,
                "provider": "curve-fit",
                "description": (
                    "The wind-pressure relationship, fitted rather than "
                    "asserted: least squares over the historical record, "
                    "reported with R² so the fit can be judged."
                ),
                "configuration": {
                    "x": "min_pressure",
                    "y": "wind_ms",
                    "family": "linear",
                    "predict_for": [900, 950, 1000],
                },
                "tags": ["typhoon", "mathematical"],
            },
        ],
        applications=[
            {
                "name": APPLICATION,
                "description": (
                    "Draw or pick a track, find the closest historical typhoons "
                    "with Coastline-RRF, and read the predicted landfall-track "
                    "category."
                ),
                "kind": "builtin",
                "models": [ANALOG_MODEL, PRECIP_MODEL],
                "datasets": [DATASET],
                #  Taken from the climatology seeder rather than written out,
                #  because a name typed twice is a name that drifts - and a
                #  dashboard reference that matches nothing fails silently,
                #  leaving an application with nothing to show.
                "dashboards": [board["name"] for board in DASHBOARDS],
                "entrypoint": "/applications/typhoon",
                "configuration": {
                    "default_method": "coastline_rrf",
                    "default_buffer_km": 500,
                },
                "publish": True,
            }
        ],
    )
