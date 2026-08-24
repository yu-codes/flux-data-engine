"""background jobs

A pipeline run, an experiment run and a report export all used to happen inside
the HTTP request that asked for them. This table is the record of that work
happening somewhere else: what kind, what it is working on, whether it is still
going, and what it produced.

Revision ID: c8f2a1e4b7d3
Revises: b41c7de90a12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c8f2a1e4b7d3"
down_revision: str | None = "b41c7de90a12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        #  A string rather than an enum: the set of kinds is decided by which
        #  handlers the deployment registers, not by the schema. Adding one
        #  must not need a migration.
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("outcome", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_by", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_kind", "jobs", ["kind"])
    op.create_index("ix_jobs_target_id", "jobs", ["target_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_target_id", table_name="jobs")
    op.drop_index("ix_jobs_kind", table_name="jobs")
    op.drop_table("jobs")
