"""execution cancellation and heartbeat

Two columns that make two existing features honest.

`cancel_requested` separates "somebody asked this to stop" from "this stopped".
Cancelling a running execution used to write the terminal status directly,
which the worker then overwrote when it finished the work it was never told to
abandon.

`heartbeat_at` records when the worker holding a RUNNING execution last said it
was alive. Without it, an execution whose worker was killed stayed RUNNING for
ever: the recovery sweep only looked at PENDING rows, and nothing on a RUNNING
row could distinguish a long job from a dead one.

Revision ID: b41c7de90a12
Revises: 976f1929ff66
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b41c7de90a12"
down_revision: str | None = "976f1929ff66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "executions",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )

    #  Rows that were RUNNING when this ran have no worker behind them - the
    #  process that would have written their heartbeat predates the column.
    #  Seeding the heartbeat from started_at lets the reclaim sweep judge them
    #  by the same rule as everything else instead of special-casing history.
    op.execute(
        "UPDATE executions SET heartbeat_at = started_at "
        "WHERE status = 'running' AND started_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("executions", "heartbeat_at")
    op.drop_column("executions", "cancel_requested")
