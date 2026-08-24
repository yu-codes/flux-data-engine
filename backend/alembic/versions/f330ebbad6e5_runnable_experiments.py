"""runnable experiments

Revision ID: f330ebbad6e5
Revises: 565db84248eb
Create Date: 2026-08-22 17:11:23.092929
"""

from __future__ import annotations

from collections.abc import Sequence

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'f330ebbad6e5'
down_revision: str | None = '565db84248eb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Turn a bag of model ids into a list of trials.

    An experiment used to hold `model_ids` and nothing else — no parameters, no
    dataset, nothing to run. Each of those ids becomes a trial with default
    parameters, which is exactly what it meant before, so no comparison loses
    its members.

    `trials` is added nullable, backfilled, then made non-null: adding a
    non-null column to a populated table fails otherwise.
    """
    op.add_column(
        "experiments", sa.Column("dataset_version_id", sa.String(length=64), nullable=True)
    )
    op.add_column("experiments", sa.Column("trials", sa.JSON(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, model_ids FROM experiments")).fetchall()
    for experiment_id, model_ids in rows:
        parsed = json.loads(model_ids) if isinstance(model_ids, str) else (model_ids or [])
        trials = [
            {
                "model_id": model_id,
                "label": "",
                "parameters": {},
                "model_version_id": None,
                "kind": None,
            }
            for model_id in parsed
        ]
        connection.execute(
            sa.text("UPDATE experiments SET trials = :trials WHERE id = :id").bindparams(
                #  Typed, not stringified: Postgres refuses a varchar into a
                #  json column, and SQLAlchemy serialises per dialect.
                sa.bindparam("trials", value=trials, type_=sa.JSON),
                sa.bindparam("id", value=experiment_id),
            )
        )

    connection.execute(
        sa.text("UPDATE experiments SET trials = :empty WHERE trials IS NULL").bindparams(
            sa.bindparam("empty", value=[], type_=sa.JSON)
        )
    )
    with op.batch_alter_table("experiments") as batch:
        batch.alter_column("trials", nullable=False)
    op.drop_column("experiments", "model_ids")


def downgrade() -> None:
    op.add_column("experiments", sa.Column("model_ids", sa.JSON(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, trials FROM experiments")).fetchall()
    for experiment_id, trials in rows:
        parsed = json.loads(trials) if isinstance(trials, str) else (trials or [])
        ids = [t["model_id"] for t in parsed if isinstance(t, dict) and t.get("model_id")]
        connection.execute(
            sa.text("UPDATE experiments SET model_ids = :ids WHERE id = :id").bindparams(
                sa.bindparam("ids", value=ids, type_=sa.JSON),
                sa.bindparam("id", value=experiment_id),
            )
        )
    op.drop_column("experiments", "trials")
    op.drop_column("experiments", "dataset_version_id")
