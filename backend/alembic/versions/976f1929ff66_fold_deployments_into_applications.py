"""fold deployments into applications

Revision ID: 976f1929ff66
Revises: f330ebbad6e5
Create Date: 2026-08-22 17:26:03.153639
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '976f1929ff66'
down_revision: str | None = 'f330ebbad6e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the deployments table.

    A deployment recorded an environment that was always "local", a version that
    counted publishes, an endpoint copied from the application, and a status of
    active/stopped beside the application's own draft/published. No deploy
    mechanism sat behind any of it — creating one made nothing happen.

    Any application that had an active deployment is published, so nothing that
    was reachable stops being reachable.
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if "deployments" not in inspector.get_table_names():
        return

    connection.execute(
        sa.text(
            "UPDATE applications SET status = 'published' "
            "WHERE status <> 'published' AND id IN ("
            "  SELECT application_id FROM deployments WHERE status = 'active'"
            ")"
        )
    )
    op.drop_table("deployments")


def downgrade() -> None:
    """Recreate the table, empty.

    The records cannot be reconstructed: an application's published state says
    nothing about which environment or version a deployment claimed.
    """
    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("application_id", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
