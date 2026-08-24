"""application sharing

An Application could be published, which made it reachable by people who
already had accounts. It could not be shown to anybody else at all, so the last
link of the product chain stopped at the platform's own front door.

A share token is a capability: holding the URL is the permission. Unique and
indexed because the public route looks an application up by it, and nullable
because most applications are never shared.

Revision ID: b8d1a4f92e57
Revises: a1e5f7b23c60
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8d1a4f92e57"
down_revision: str | None = "a1e5f7b23c60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "visibility", sa.String(16), nullable=False, server_default="workspace"
        ),
    )
    op.add_column(
        "applications", sa.Column("share_token", sa.String(64), nullable=True)
    )
    op.add_column(
        "applications", sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True)
    )
    #  Unique: two applications answering to one link would be a bug nobody
    #  could see from either end.
    op.create_index(
        "ix_applications_share_token", "applications", ["share_token"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_applications_share_token", table_name="applications")
    op.drop_column("applications", "shared_at")
    op.drop_column("applications", "share_token")
    op.drop_column("applications", "visibility")
