"""an execution runs a runnable, not only a model

A Model and a Pipeline both fit the platform's own formula - inputs,
parameters, a versioned definition, an output - but only one of them could be
executed, scheduled, compared or served. Every horizontal capability had to be
built twice or denied to the second, and a third runnable would have cost a
third implementation of each.

`model_id` becomes `target_id`, and `target_type` says what the id refers to.
Every row that exists today ran a model, which is what the default records.

Revision ID: e2c9a41f7b83
Revises: d7f3b18c60ae
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e2c9a41f7b83"
down_revision: str | None = "d7f3b18c60ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    #  Batch mode throughout: SQLite cannot ALTER COLUMN, and this project
    #  documents SQLite as the way to run without PostgreSQL.
    with op.batch_alter_table("executions") as batch:
        batch.add_column(
            sa.Column("target_type", sa.String(length=16), nullable=False,
                      server_default="model")
        )
        batch.alter_column("model_id", existing_type=sa.String(length=64),
                           new_column_name="target_id")
    with op.batch_alter_table("executions") as batch:
        batch.alter_column("target_type", existing_type=sa.String(length=16),
                           server_default=None)
    op.create_index("ix_executions_target_type", "executions", ["target_type"])

    #  A pipeline run can now be the work behind an ordinary Execution, so the
    #  two need to be able to find each other afterwards.
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(sa.Column("execution_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_column("execution_id")
    op.drop_index("ix_executions_target_type", table_name="executions")
    with op.batch_alter_table("executions") as batch:
        batch.alter_column("target_id", existing_type=sa.String(length=64),
                           new_column_name="model_id")
        batch.drop_column("target_type")
