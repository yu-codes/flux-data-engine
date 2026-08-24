"""schedule targets, and what a pipeline run ran

A schedule named a model and nothing else, so the most ordinary recurring job a
data platform is asked for - re-run this pipeline every morning - could not be
expressed. The trigger, the cadence and the bookkeeping are the same for both;
only what gets submitted differs.

`model_id` becomes `target_id`, and `target_type` says what the id refers to.
Existing rows are all models, which is what the default records.

A pipeline run also gains `definition_snapshot`, for the same reason a
ModelVersion has one: edit a pipeline and every past run silently starts
describing itself with the new steps. Existing runs get an empty snapshot -
their definition is genuinely unknown, and saying so is better than implying
the current one was what ran.

Revision ID: c4a7e2b95d18
Revises: b8d1a4f92e57
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4a7e2b95d18"
down_revision: str | None = "b8d1a4f92e57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    #  Added with a server default so existing rows are valid the moment the
    #  column exists, then dropped: the application always writes the value,
    #  and a default left in place is a default something eventually relies on.
    #  Batch mode throughout: SQLite has no ALTER COLUMN, and this project
    #  documents SQLite as a way to run without PostgreSQL.
    with op.batch_alter_table("schedules") as batch:
        batch.add_column(
            sa.Column("target_type", sa.String(length=16), nullable=False,
                      server_default="model")
        )
        batch.alter_column("model_id", existing_type=sa.String(length=64),
                           new_column_name="target_id")
    #  Dropped afterwards: the default exists to make the backfill valid, and a
    #  default left in place is one something eventually relies on.
    with op.batch_alter_table("schedules") as batch:
        batch.alter_column("target_type", existing_type=sa.String(length=16),
                           server_default=None)

    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(
            sa.Column("definition_snapshot", sa.JSON(), nullable=False,
                      server_default="{}")
        )
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.alter_column("definition_snapshot", existing_type=sa.JSON(),
                           server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_column("definition_snapshot")
    with op.batch_alter_table("schedules") as batch:
        batch.alter_column("target_id", existing_type=sa.String(length=64),
                           new_column_name="model_id")
        batch.drop_column("target_type")
