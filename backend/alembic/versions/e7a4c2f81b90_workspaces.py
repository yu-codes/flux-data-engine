"""workspaces and resource ownership

Every named thing was globally unique and belonged to nobody. Two people could
not both have a dataset called "Sales", a team could not keep dev and prod
copies of a pipeline, and "who created this" was answerable only by reading the
audit log sideways.

Three changes. A workspace table and a membership table; a `workspace_id` and a
`created_by` on every owned resource; and the global unique constraints on
`name` and `slug` dropped, because a name is unique inside a workspace now and
a database-wide constraint would contradict that.

Everything that exists is placed in the default workspace, which is what it
effectively already was.

Revision ID: e7a4c2f81b90
Revises: d3b6e9c41f57
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "e7a4c2f81b90"
down_revision: str | None = "d3b6e9c41f57"
branch_labels = None
depends_on = None

DEFAULT_WORKSPACE_ID = "ws_default"

#  Every table whose rows belong to somebody. `users` and `audit_entries` are
#  installation-wide by nature; the child tables - model_versions,
#  dataset_versions, pipeline_runs - reach their workspace through their
#  parent, and a second copy of that fact would be a second thing to keep true.
OWNED = [
    "sources",
    "data_schemas",
    "datasets",
    "models",
    "experiments",
    "evaluations",
    "executions",
    "results",
    "reports",
    "visualizations",
    "dashboards",
    "applications",
    "pipelines",
    "schedules",
    "jobs",
]

#  Global uniqueness that becomes per-workspace. Postgres names a column
#  constraint `<table>_<column>_key` by default.
UNIQUE_COLUMNS = {
    "sources": ["name"],
    "datasets": ["name"],
    "models": ["name", "slug"],
    "experiments": ["name"],
    "reports": ["name"],
    "dashboards": ["name"],
    "applications": ["name", "slug"],
    "pipelines": ["name"],
    "schedules": ["name"],
}


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspace_members",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_member"),
    )
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    #  The timestamp is bound rather than written as NOW(): SQLite has no such
    #  function, and this project offers SQLite as the way to run without
    #  PostgreSQL. `true` likewise becomes a bound 1/True.
    now = datetime.now(UTC)
    op.execute(
        sa.text(
            "INSERT INTO workspaces "
            "(id, name, slug, description, is_default, created_at, updated_at) "
            "VALUES (:id, :name, :slug, :description, :is_default, :now, :now)"
        ).bindparams(
            is_default=True,
            now=now,
            id=DEFAULT_WORKSPACE_ID,
            name="Default workspace",
            slug="default-workspace",
            description=(
                "Everything that existed before workspaces did, and everything "
                "created without naming one."
            ),
        )
    )

    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    for table in OWNED:
        if table not in existing:
            continue
        columns = {c["name"] for c in inspector.get_columns(table)}
        op.add_column(table, sa.Column("workspace_id", sa.String(64), nullable=True))
        #  A few tables already recorded their creator before workspaces
        #  existed; adding it again would fail rather than be harmless.
        if "created_by" not in columns:
            op.add_column(table, sa.Column("created_by", sa.String(64), nullable=True))
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
        #  Everything that exists belongs where it effectively already was.
        op.execute(
            sa.text(f"UPDATE {table} SET workspace_id = :ws").bindparams(
                ws=DEFAULT_WORKSPACE_ID
            )
        )

    #  A name is unique inside a workspace now.
    for table, columns in UNIQUE_COLUMNS.items():
        if table not in existing:
            continue
        names = {c["name"] for c in inspector.get_unique_constraints(table)}
        for column in columns:
            for candidate in (f"{table}_{column}_key", f"uq_{table}_{column}"):
                if candidate in names:
                    op.drop_constraint(candidate, table, type_="unique")
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_table_names())

    for table, columns in UNIQUE_COLUMNS.items():
        if table not in existing:
            continue
        for column in columns:
            op.drop_index(f"ix_{table}_{column}", table_name=table)
            #  Batch mode: SQLite cannot add a constraint to an existing table,
            #  and alembic's copy-and-move gets there.
            with op.batch_alter_table(table) as batch:
                batch.create_unique_constraint(f"{table}_{column}_key", [column])

    for table in OWNED:
        if table not in existing:
            continue
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("created_by")
            batch.drop_column("workspace_id")

    op.drop_table("workspace_members")
    op.drop_table("workspaces")
