"""Where this plugin's data lives.

The platform's configuration must not name a domain, so the plugin owns its own
path and asks the platform only for the data root — which is a platform
concern. Same arrangement as every other built-in application.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings

#  The project this application's data belongs to. Declared here as well as in
#  the fixture because a plugin ships files at a path it has to know before any
#  database exists — and because a name typed twice is a name that drifts, the
#  fixture imports it from here rather than repeating it.
PROJECT = "AssetGuard"
SUBDIRECTORY = (PROJECT, "sources")


def data_dir() -> Path:
    """The directory holding the fleet record."""
    return get_settings().data_root.joinpath(*SUBDIRECTORY)


def relative(filename: str) -> str:
    """A path as a Source's connection states it: relative to the data root."""
    return "/".join((*SUBDIRECTORY, filename))
