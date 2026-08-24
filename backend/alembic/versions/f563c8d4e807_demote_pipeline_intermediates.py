"""demote pipeline intermediates

Revision ID: f563c8d4e807
Revises: 2f97bd4eacab
Create Date: 2026-08-21 18:14:58.743715
"""

from __future__ import annotations

from collections.abc import Sequence

import json

import sqlalchemy as sa
from alembic import op


revision: str = 'f563c8d4e807'
down_revision: str | None = '2f97bd4eacab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Re-classify datasets that were only ever a step on the way.

    Terminal outputs are what a pipeline exists to produce and stay as they
    are; every other step output is working state. `pipeline_runs` records both
    facts, so the run history is what this reads rather than a naming guess.
    """
    connection = op.get_bind()

    deliverables: set[str] = set()
    produced: set[str] = set()
    rows = connection.execute(
        sa.text("SELECT step_runs, output_dataset_ids FROM pipeline_runs")
    )
    for step_runs, outputs in rows:
        steps = json.loads(step_runs) if isinstance(step_runs, str) else (step_runs or [])
        ends = json.loads(outputs) if isinstance(outputs, str) else (outputs or [])
        deliverables.update(i for i in ends if i)
        for step in steps:
            if isinstance(step, dict) and step.get("dataset_id"):
                produced.add(step["dataset_id"])

    intermediates = tuple(produced - deliverables)
    if intermediates:
        connection.execute(
            sa.text(
                "UPDATE datasets SET origin = 'intermediate' "
                "WHERE origin = 'execution' AND id IN :ids"
            ).bindparams(sa.bindparam("ids", value=intermediates, expanding=True))
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("UPDATE datasets SET origin = 'execution' WHERE origin = 'intermediate'")
    )
