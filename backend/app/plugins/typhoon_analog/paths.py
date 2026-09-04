"""Where this plugin's bundled data lives.

Platform configuration should not name a domain: `Settings.typhoon_data_dir`
put the word "typhoon" in the core of a general platform, so every deployment
carried one application's vocabulary whether or not it used it.

The plugin knows where its own files are. `data_root` is the only thing it
needs from the platform, and that is a platform concern.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings

#  The project this application's data belongs to. Declared here as well as in
#  the fixture because a plugin ships files at a path it has to know before any
#  database exists — and because a name typed twice is a name that drifts, the
#  fixture imports it from here rather than repeating it.
PROJECT = "HydroAnalog"
SUBDIRECTORY = (PROJECT, "sources", "preprocessed")


def typhoon_data_dir() -> Path:
    """The directory holding the preprocessed CWA and IBTrACS records."""
    return get_settings().data_root.joinpath(*SUBDIRECTORY)
