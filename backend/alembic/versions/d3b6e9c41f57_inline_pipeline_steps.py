"""inline pipeline steps

A pipeline step used to be a ModelDefinition row with `scope = 'step'` - a
library model that was immediately hidden from the library, because a step's
configuration has no life of its own. Steps now carry their own provider and
configuration, so those rows have nothing left to be.

This migration folds each step model back into the step that used it and then
deletes the row. Nothing is lost: the provider and configuration are exactly
what the model held, and they end up on the step that was the only thing
referring to them.

Revision ID: d3b6e9c41f57
Revises: c8f2a1e4b7d3
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "d3b6e9c41f57"
down_revision: str | None = "c8f2a1e4b7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    #  Executions may now name a definition instead of a model.
    op.add_column(
        "executions", sa.Column("definition_snapshot", sa.JSON(), nullable=True)
    )
    #  Batch mode, because SQLite cannot ALTER COLUMN: alembic rebuilds the
    #  table there and passes this straight through on PostgreSQL.
    with op.batch_alter_table("executions") as batch:
        batch.alter_column("model_id", existing_type=sa.String(64), nullable=True)

    step_models = {
        row.id: {"provider": row.provider, "configuration": row.configuration or {}}
        for row in connection.execute(
            sa.text(
                "SELECT id, provider, configuration FROM models WHERE scope = 'step'"
            )
        )
    }

    #  Fold each step model into the steps that referenced it.
    for row in connection.execute(sa.text("SELECT id, steps FROM pipelines")):
        steps = row.steps if isinstance(row.steps, list) else json.loads(row.steps or "[]")
        changed = False
        for step in steps:
            folded = step_models.get(step.get("model_id"))
            if not folded:
                #  A step running a real library model keeps doing so; that is
                #  a deliberate choice and not the thing being cleaned up.
                continue
            step["provider"] = folded["provider"]
            step["configuration"] = folded["configuration"]
            step["model_id"] = None
            changed = True

        #  `materialise` used to default to true and every step wrote it, so a
        #  stored `true` says what the platform did rather than what anybody
        #  chose. It now means "keep this intermediate as well", and the ends
        #  of a run are published without being asked - so clearing it leaves
        #  each pipeline producing exactly its outputs.
        for step in steps:
            if step.get("materialise") is True:
                step["materialise"] = False
                changed = True
        if changed:
            connection.execute(
                sa.text("UPDATE pipelines SET steps = :steps WHERE id = :id").bindparams(
                    sa.bindparam("steps", value=steps, type_=sa.JSON),
                    sa.bindparam("id", value=row.id),
                )
            )

    #  Executions of those models keep their own record of what they ran, so
    #  the row can go without erasing history.
    for model_id, folded in step_models.items():
        connection.execute(
            sa.text(
                "UPDATE executions SET definition_snapshot = :snapshot, model_id = NULL "
                "WHERE model_id = :model_id"
            ).bindparams(
                sa.bindparam(
                    "snapshot",
                    value={
                        "provider": folded["provider"],
                        "configuration": folded["configuration"],
                        "name": "pipeline step",
                    },
                    type_=sa.JSON,
                ),
                sa.bindparam("model_id", value=model_id),
            )
        )

    if step_models:
        connection.execute(
            sa.text("DELETE FROM model_versions WHERE model_id IN :ids").bindparams(
                sa.bindparam("ids", value=tuple(step_models), expanding=True)
            )
        )
        connection.execute(
            sa.text("DELETE FROM models WHERE id IN :ids").bindparams(
                sa.bindparam("ids", value=tuple(step_models), expanding=True)
            )
        )

    #  With nothing producing step models, the column has no meaning left.
    #  The index goes first: SQLite keeps index definitions as text and refuses
    #  a column drop that would leave one pointing at nothing.
    op.drop_index("ix_models_scope", table_name="models")
    with op.batch_alter_table("models") as batch:
        batch.drop_column("scope")


def downgrade() -> None:
    op.add_column(
        "models",
        sa.Column("scope", sa.String(16), nullable=False, server_default="library"),
    )
    #  Put the index back too: the migration below this one drops it on its own
    #  way down, and an index that is not there is an error rather than a no-op.
    op.create_index("ix_models_scope", "models", ["scope"])
    with op.batch_alter_table("executions") as batch:
        batch.alter_column("model_id", existing_type=sa.String(64), nullable=False)
    op.drop_column("executions", "definition_snapshot")
