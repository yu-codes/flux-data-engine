"""API key table. Only the hash is stored."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.api_keys import ApiKey


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #  Indexed and unique because every authenticated request looks a key up by
    #  it, and two keys hashing the same would mean the hash is broken.
    key_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    hint: Mapped[str] = mapped_column(String(32), default="")
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def to_entity(row: ApiKeyRow) -> ApiKey:
    return ApiKey(
        id=row.id,
        name=row.name,
        workspace_id=row.workspace_id,
        key_hash=row.key_hash,
        hint=row.hint or "",
        can_write=bool(row.can_write),
        created_by=row.created_by,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


def to_row(entity: ApiKey, row: ApiKeyRow | None = None) -> ApiKeyRow:
    row = row or ApiKeyRow(id=entity.id, created_at=entity.created_at)
    row.name = entity.name
    row.workspace_id = entity.workspace_id
    row.key_hash = entity.key_hash
    row.hint = entity.hint
    row.can_write = entity.can_write
    row.created_by = entity.created_by
    row.last_used_at = entity.last_used_at
    row.expires_at = entity.expires_at
    row.revoked_at = entity.revoked_at
    return row
