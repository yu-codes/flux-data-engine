"""execution attempts

An execution that never reaches a terminal state was picked up by every
recovery sweep, indefinitely, because nothing counted how many times it had
already been tried. Counting attempts is what lets the platform give up.

Revision ID: f5c8d3e60a41
Revises: e7a4c2f81b90
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f5c8d3e60a41"
down_revision: str | None = "e7a4c2f81b90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    #  Anything that already started has been tried at least once, and saying
    #  zero would give a stuck execution a fresh set of retries it has already
    #  used.
    op.execute("UPDATE executions SET attempts = 1 WHERE started_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("executions", "attempts")
