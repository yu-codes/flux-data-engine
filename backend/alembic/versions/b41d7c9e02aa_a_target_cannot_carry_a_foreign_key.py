"""a target cannot carry a foreign key to models

`executions.target_id` inherited the foreign key that `model_id` had while a
model was the only thing an execution could run. What the id points at now
depends on `target_type`, and no column can reference two tables: the first
pipeline execution against PostgreSQL was rejected by that constraint.

SQLite does not enforce foreign keys unless each connection asks it to, which
is why nothing said so until the deployment did. The application now asks it
to, so a SQLite deployment carrying this constraint would start rejecting
pipeline executions too - hence the table is rebuilt there rather than left
alone.

Revision ID: b41d7c9e02aa
Revises: e2c9a41f7b83
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b41d7c9e02aa"
down_revision: str | None = "e2c9a41f7b83"
branch_labels = None
depends_on = None


def _executions_without_target_foreign_keys(bind) -> sa.Table:
    """The table as it should be: the same columns, minus the target's key."""
    table = sa.Table("executions", sa.MetaData(), autoload_with=bind)
    column = table.c.target_id
    column.foreign_keys.clear()
    table.constraints = {
        constraint
        for constraint in table.constraints
        if not (
            isinstance(constraint, sa.ForeignKeyConstraint)
            and "target_id" in constraint.column_keys
        )
    }
    return table


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        #  Named by the column it was created for, years before the rename.
        op.drop_constraint("executions_model_id_fkey", "executions", type_="foreignkey")
    else:
        #  SQLite cannot drop a constraint; batch mode rebuilds the table from
        #  the definition handed to it, which is this one without the key.
        with op.batch_alter_table(
            "executions", copy_from=_executions_without_target_foreign_keys(bind)
        ) as batch:
            batch.alter_column("target_id", existing_type=sa.String(length=64))

    #  Filtering runs by what produced them is what the column is for, and it
    #  lost the index that came free with the foreign key.
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("executions")}
    if "ix_executions_target_id" not in existing:
        op.create_index("ix_executions_target_id", "executions", ["target_id"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = {index["name"] for index in sa.inspect(bind).get_indexes("executions")}
    if "ix_executions_target_id" in existing:
        op.drop_index("ix_executions_target_id", table_name="executions")

    #  Only rows that ran a model can satisfy the constraint being restored.
    op.execute("DELETE FROM executions WHERE target_type <> 'model'")
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "executions_model_id_fkey",
            "executions",
            "models",
            ["target_id"],
            ["id"],
            ondelete="CASCADE",
        )
    else:
        table = sa.Table("executions", sa.MetaData(), autoload_with=bind)
        table.append_constraint(
            sa.ForeignKeyConstraint(
                ["target_id"], ["models.id"], ondelete="CASCADE",
                name="executions_model_id_fkey",
            )
        )
        with op.batch_alter_table("executions", copy_from=table) as batch:
            batch.alter_column("target_id", existing_type=sa.String(length=64))
