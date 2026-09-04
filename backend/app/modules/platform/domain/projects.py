"""Projects: which piece of work a resource belongs to.

A workspace answers "whose is this" — it is a boundary, with members, roles
and an isolation guarantee. A project answers a different question: "which
piece of work is this part of". One team, one workspace, and inside it a
typhoon study, a maintenance programme and a pile of demo data that should not
be shown to each other by default.

The two are deliberately not the same mechanism, and the difference is worth
stating because it decides how each behaves:

* **A workspace is a boundary.** Reading a row by an id from another workspace
  is refused. Getting that wrong is a security failure.
* **A project is a filing system.** It filters what a list shows. Reading a
  row by id still works, because a report may cite a dataset from another
  project, lineage may walk into one, and an application may bundle several.
  Refusing those would break real things to enforce a rule nobody asked for.

Not everything is filed. Reports, schedules and applications are not, because
each is already about one subject; model *definitions* are optionally filed,
because a formula is genuinely reusable and a backtest of one fleet is not.
What is filed is the material a project accumulates — its sources, datasets,
pipelines, charts, dashboards and the runs over them — which is exactly the
material that becomes unmanageable as cases are added.

A project also owns a directory of its own under the data root. Source files
are per-project on disk for the same reason the rows are: so that adding the
fourth case does not mean picking through three cases' files to find one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.shared.errors import ValidationError
from app.shared.ids import new_id, slugify, utcnow

#  Every workspace has one. It is what a resource created without naming a
#  project belongs to, and what a fresh installation puts its sample data in.
DEFAULT_PROJECT_NAME = "Demo"

#  A directory name is a path segment the platform will join onto the data
#  root, so it is checked rather than trusted: no separators, no traversal, no
#  leading dot, and nothing a filesystem will argue about.
_DIRECTORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#  Where a project's own files live, relative to its directory. `sources` holds
#  what a Source reads; `uploads` holds what somebody put there through the UI.
#  Datasets are not here: they are immutable Parquet in the object store, which
#  may be a local directory or S3, and the platform must not care which.
SOURCES_SUBDIRECTORY = "sources"
UPLOADS_SUBDIRECTORY = "uploads"


def check_directory(name: str) -> str:
    """Validate a directory name, or say precisely what is wrong with it."""
    if not _DIRECTORY.match(name or ""):
        raise ValidationError(
            f"'{name}' is not a usable directory name: use letters, digits, "
            f"dot, dash or underscore, starting with a letter or digit"
        )
    return name


def default_directory(name: str) -> str:
    """A directory name derived from a project's own name.

    Kept close to what the person typed — "HydroAnalog" stays "HydroAnalog" —
    because somebody is going to open this directory in a file browser and
    should recognise it. Only characters a path cannot carry are replaced.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-._")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = slugify(name) or new_id("proj")
    return cleaned[:64]


@dataclass
class Project:
    """One piece of work, and the directory its source files live in."""

    name: str
    slug: str = ""
    description: str = ""
    #  A path segment under the data root. Named separately from the slug
    #  because a slug is for URLs and a directory is for people.
    directory: str = ""
    #  The workspace's default cannot be deleted: something has to own a
    #  resource created without naming a project.
    is_default: bool = False
    id: str = field(default_factory=lambda: new_id("proj"))
    workspace_id: str | None = None
    created_by: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.directory:
            self.directory = default_directory(self.name)
        check_directory(self.directory)

    @property
    def sources_path(self) -> str:
        """This project's source files, relative to the data root."""
        return f"{self.directory}/{SOURCES_SUBDIRECTORY}"

    @property
    def uploads_path(self) -> str:
        return f"{self.directory}/{UPLOADS_SUBDIRECTORY}"

    def source_file(self, *parts: str) -> str:
        """The connection path a Source in this project should be given."""
        return "/".join((self.directory, SOURCES_SUBDIRECTORY, *parts))
