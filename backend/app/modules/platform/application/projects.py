"""Creating projects, and keeping their directories in step with them."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import slugify, utcnow

from ..domain.projects import (
    DEFAULT_PROJECT_NAME,
    SOURCES_SUBDIRECTORY,
    UPLOADS_SUBDIRECTORY,
    Project,
    check_directory,
    default_directory,
)

logger = logging.getLogger(__name__)

#  What a project may hold. Injected by the composition root rather than
#  imported, because `platform` sits at the bottom of the dependency stack and
#  must not learn what a dataset is in order to refuse deleting one.
UsageCounter = Callable[[str], dict[str, int]]


class ProjectService:
    """Projects, the directories they own, and what may be deleted."""

    def __init__(
        self,
        repository,
        *,
        data_root: Path | None = None,
        usage: UsageCounter | None = None,
    ) -> None:
        self.repository = repository
        self.data_root = data_root
        self.usage = usage

    # -- reads -------------------------------------------------------------
    def get(self, project_id: str) -> Project:
        project = self.repository.get(project_id) or self.repository.get_by_slug(
            project_id
        )
        if not project:
            raise NotFoundError(f"project '{project_id}' not found")
        return project

    def find(self, project_id: str | None) -> Project | None:
        """The project named, without raising when nothing is named."""
        if not project_id:
            return None
        return self.repository.get(project_id) or self.repository.get_by_slug(project_id)

    def list(self) -> list[Project]:
        return self.repository.list()

    def default(self) -> Project:
        """The project a resource belongs to when nobody said otherwise.

        Created on first use, like the default workspace. Named generically on
        purpose: the core of a general platform must not know that any
        particular piece of work exists, so the domains that do — the built-in
        applications — declare their own projects in their fixtures.
        """
        existing = self.repository.get_default()
        if existing:
            self.ensure_directory(existing)
            return existing
        project = self.repository.add(
            Project(
                name=DEFAULT_PROJECT_NAME,
                slug=slugify(DEFAULT_PROJECT_NAME),
                description=(
                    "Sample and scratch material: what a fresh installation "
                    "starts with, and where anything created without naming a "
                    "project goes."
                ),
                is_default=True,
            )
        )
        self.ensure_directory(project)
        return project

    # -- writes ------------------------------------------------------------
    def create(
        self,
        *,
        name: str,
        description: str = "",
        directory: str | None = None,
        created_by: str | None = None,
    ) -> Project:
        if not (name or "").strip():
            raise ValidationError("a project needs a name")
        if self.repository.get_by_name(name):
            raise ConflictError(f"a project named '{name}' already exists")
        wanted = check_directory(directory or default_directory(name))
        clash = next(
            (p for p in self.repository.list() if p.directory == wanted), None
        )
        if clash is not None:
            raise ConflictError(
                f"the '{clash.name}' project already uses the '{wanted}' directory"
            )
        project = self.repository.add(
            Project(
                name=name.strip(),
                description=description,
                directory=wanted,
                created_by=created_by,
            )
        )
        self.ensure_directory(project)
        logger.info("created project '%s' in %s/", project.name, project.directory)
        return project

    def update(self, project_id: str, changes: dict) -> Project:
        project = self.get(project_id)
        if changes.get("name"):
            other = self.repository.get_by_name(changes["name"])
            if other and other.id != project.id:
                raise ConflictError(
                    f"a project named '{changes['name']}' already exists"
                )
            project.name = changes["name"].strip()
            project.slug = slugify(project.name)
        if changes.get("description") is not None:
            project.description = changes["description"]
        if changes.get("directory") and changes["directory"] != project.directory:
            #  Renaming the directory moves the files with it. Leaving them
            #  behind would give the project an empty directory and orphan
            #  every source path that already points into the old one.
            wanted = check_directory(changes["directory"])
            clash = next(
                (p for p in self.repository.list()
                 if p.directory == wanted and p.id != project.id),
                None,
            )
            if clash is not None:
                raise ConflictError(
                    f"the '{clash.name}' project already uses the '{wanted}' directory"
                )
            self._move_directory(project.directory, wanted)
            project.directory = wanted
        project.updated_at = utcnow()
        updated = self.repository.update(project)
        self.ensure_directory(updated)
        return updated

    def delete(self, project_id: str) -> None:
        project = self.get(project_id)
        if project.is_default:
            raise ConflictError(
                "the default project cannot be deleted: something has to own "
                "resources created without naming one"
            )
        held = self.usage(project.id) if self.usage else {}
        occupied = {kind: count for kind, count in held.items() if count}
        if occupied:
            #  Refused rather than cascaded. Deleting a project should not be
            #  a way to delete forty datasets by accident, and moving them
            #  somewhere is a decision the person has to make.
            raise ConflictError(
                f"'{project.name}' still holds "
                + ", ".join(f"{count} {kind}" for kind, count in sorted(occupied.items()))
                + "; move or delete them first",
                details={"holds": occupied},
            )
        self.repository.delete(project.id)
        logger.info("deleted project '%s'", project.name)

    def holdings(self, project_id: str) -> dict[str, int]:
        """What this project holds, for the UI to show before a deletion."""
        return self.usage(project_id) if self.usage else {}

    # -- the directory -----------------------------------------------------
    def ensure_directory(self, project: Project) -> Path | None:
        """Create the project's own directories if they are not there yet."""
        if self.data_root is None:
            return None
        root = self.data_root / project.directory
        for name in (SOURCES_SUBDIRECTORY, UPLOADS_SUBDIRECTORY):
            (root / name).mkdir(parents=True, exist_ok=True)
        return root

    def _move_directory(self, old: str, new: str) -> None:
        if self.data_root is None:
            return
        source = self.data_root / old
        target = self.data_root / new
        if not source.exists():
            return
        if target.exists():
            raise ConflictError(f"the '{new}' directory already exists on disk")
        shutil.move(str(source), str(target))
        logger.info("moved project files from %s/ to %s/", old, new)
