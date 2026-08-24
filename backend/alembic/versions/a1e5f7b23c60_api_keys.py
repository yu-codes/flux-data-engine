"""api keys

The only way to call a model from another system was to exchange a person's
password for a JWT and send that. A key belongs to a workspace rather than to
a person, so it can be scoped, expired and revoked without touching anybody's
account.

Only the hash is stored: a leaked database yields nothing that works.

Revision ID: a1e5f7b23c60
Revises: f5c8d3e60a41
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a1e5f7b23c60"
down_revision: str | None = "f5c8d3e60a41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        #  Unique because every authenticated request looks a key up by it, and
        #  two keys sharing a hash would mean the hash is broken.
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("hint", sa.String(32), nullable=True),
        sa.Column("can_write", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_workspace_id", table_name="api_keys")
    op.drop_table("api_keys")
