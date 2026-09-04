"""Projects: which piece of work a resource belongs to.

A workspace says whose a resource is; a project says which piece of work it is
part of. The two are separate mechanisms on purpose — see
`app/modules/platform/domain/projects.py` — and this migration adds the second
without touching the first.

Existing rows are filed into the workspace's default project rather than left
unfiled. Unfiled would technically work, since a null project shows in every
project, but it would mean an installation upgraded from before this change
had a "Demo" project that was empty while everything sat outside it — which
reads as a bug and is a poor first impression of a feature.

Revision ID: c1d94f0a7e35
Revises: b41d7c9e02aa
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC

import sqlalchemy as sa

from alembic import op

revision: str = "c1d94f0a7e35"
down_revision: str | None = "b41d7c9e02aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#  Every table a project files. Reports, schedules, applications, jobs, users
#  and audit are deliberately absent: each is either about one subject already
#  or belongs to the installation rather than to a piece of work.
FILED = (
    "sources",
    "datasets",
    "pipelines",
    "visualizations",
    "dashboards",
    "models",
    "executions",
    "results",
    "experiments",
    "evaluations",
)

DEFAULT_PROJECT_NAME = "Demo"


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("directory", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_project_name"),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_project_slug"),
        sa.UniqueConstraint("workspace_id", "directory", name="uq_project_directory"),
    )
    op.create_index("ix_projects_workspace_id", "projects", ["workspace_id"])

    for table in FILED:
        #  batch_alter_table because SQLite cannot ALTER a column in place, and
        #  this project documents SQLite as the way to run without PostgreSQL.
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("project_id", sa.String(length=64), nullable=True))
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])

    _file_existing_rows()


def _file_existing_rows() -> None:
    """Give every workspace a default project and file its rows into it."""
    connection = op.get_bind()
    workspaces = connection.execute(
        sa.text("SELECT id FROM workspaces")
    ).fetchall()
    if not workspaces:
        return

    stamp = _timestamp()
    for index, (workspace_id,) in enumerate(workspaces):
        project_id = f"proj_default_{index:04d}"
        connection.execute(
            sa.text(
                "INSERT INTO projects "
                "(id, workspace_id, name, slug, directory, description, "
                " is_default, created_at, updated_at) "
                "VALUES (:id, :ws, :name, :slug, :dir, :desc, :dflt, :now, :now)"
            ),
            {
                "id": project_id,
                "ws": workspace_id,
                "name": DEFAULT_PROJECT_NAME,
                "slug": DEFAULT_PROJECT_NAME.lower(),
                "dir": DEFAULT_PROJECT_NAME,
                "desc": (
                    "Sample and scratch material: what a fresh installation "
                    "starts with, and where anything created without naming a "
                    "project goes."
                ),
                "dflt": True,
                #  Bound rather than `NOW()`: SQLite has no such function, and
                #  a migration that only runs on PostgreSQL is half a migration.
                "now": stamp,
            },
        )
        for table in FILED:
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET project_id = :project "  # noqa: S608
                    f"WHERE workspace_id = :ws AND project_id IS NULL"
                ),
                {"project": project_id, "ws": workspace_id},
            )


def _timestamp():
    """Bound rather than `NOW()`: SQLite has no such function."""
    from datetime import datetime

    return datetime.now(UTC)


def downgrade() -> None:
    for table in FILED:
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("project_id")
    op.drop_index("ix_projects_workspace_id", table_name="projects")
    op.drop_table("projects")
