"""an experiment says which way its primary metric is better

The leaderboard sorted by `-value`, so an experiment whose primary metric was
RMSE ranked the worst trial first and called it the leader. Direction belongs
to the comparison rather than to the metric's name, so it is stored on the
experiment that defines the comparison.

Existing rows get "higher", which is what the old sort assumed and therefore
what every experiment recorded so far was written against.

Revision ID: d7f3b18c60ae
Revises: c4a7e2b95d18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d7f3b18c60ae"
down_revision: str | None = "c4a7e2b95d18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("experiments") as batch:
        batch.add_column(
            sa.Column("primary_direction", sa.String(length=8), nullable=False,
                      server_default="higher")
        )
    with op.batch_alter_table("experiments") as batch:
        batch.alter_column("primary_direction", existing_type=sa.String(length=8),
                           server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("experiments") as batch:
        batch.drop_column("primary_direction")
