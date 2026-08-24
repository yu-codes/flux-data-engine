"""SQL persistence for API keys."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.api_keys import ApiKey
from . import api_key_orm as orm


class SqlApiKeyRepository:
    """Not workspace-scoped: a key is looked up before the workspace is known.

    The key itself says which workspace it belongs to, and that is what the
    scope is then built from - so scoping the lookup by workspace would be
    asking the question in the wrong order.
    """

    def __init__(self, session: Session):
        self.session = session

    def add(self, key: ApiKey) -> ApiKey:
        self.session.add(orm.to_row(key))
        self.session.flush()
        return key

    def get(self, key_id: str) -> ApiKey | None:
        row = self.session.get(orm.ApiKeyRow, key_id)
        return orm.to_entity(row) if row else None

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        row = self.session.scalar(
            select(orm.ApiKeyRow).where(orm.ApiKeyRow.key_hash == key_hash)
        )
        return orm.to_entity(row) if row else None

    def update(self, key: ApiKey) -> ApiKey:
        row = self.session.get(orm.ApiKeyRow, key.id)
        if row is None:
            return self.add(key)
        orm.to_row(key, row)
        self.session.flush()
        return orm.to_entity(row)

    def list(self, *, workspace_id: str | None = None) -> list[ApiKey]:
        stmt = select(orm.ApiKeyRow)
        if workspace_id:
            stmt = stmt.where(orm.ApiKeyRow.workspace_id == workspace_id)
        stmt = stmt.order_by(orm.ApiKeyRow.created_at.desc())
        return [orm.to_entity(row) for row in self.session.scalars(stmt)]

    def delete(self, key_id: str) -> None:
        row = self.session.get(orm.ApiKeyRow, key_id)
        if row is not None:
            self.session.delete(row)
            self.session.flush()
