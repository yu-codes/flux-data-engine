"""SQLAlchemy repositories for the model module."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import ModelDefinition, ModelVersion
from . import orm


class SqlModelRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, model: ModelDefinition) -> ModelDefinition:
        self.session.add(self._stamp(orm.model_to_row(model)))
        self.session.flush()
        return model

    def get(self, model_id: str) -> ModelDefinition | None:
        row = self._fetch(orm.ModelRow, model_id)
        return orm.model_to_entity(row) if row else None

    def get_by_slug(self, slug: str) -> ModelDefinition | None:
        row = self.session.scalar(
            self._scoped(select(orm.ModelRow), orm.ModelRow)
            .where(orm.ModelRow.slug == slug)
        )
        return orm.model_to_entity(row) if row else None

    def get_by_name(self, name: str) -> ModelDefinition | None:
        row = self.session.scalar(
            self._scoped(select(orm.ModelRow), orm.ModelRow)
            .where(orm.ModelRow.name == name)
        )
        return orm.model_to_entity(row) if row else None

    def list(
        self,
        *,
        model_type: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ):
        stmt = self._scoped(select(orm.ModelRow), orm.ModelRow)
        if model_type:
            stmt = stmt.where(orm.ModelRow.type == model_type)
        if provider:
            stmt = stmt.where(orm.ModelRow.provider == provider)
        if status:
            stmt = stmt.where(orm.ModelRow.status == status)
        if search:
            #  Matching name and description together is what a person means by
            #  search; matching the id as well would only surface noise.
            like = f"%{search.lower()}%"
            stmt = stmt.where(
                func.lower(orm.ModelRow.name).like(like)
                | func.lower(orm.ModelRow.description).like(like)
            )
        rows = self.session.scalars(stmt.order_by(orm.ModelRow.created_at.desc())).all()
        return [orm.model_to_entity(r) for r in rows]

    def update(self, model: ModelDefinition) -> ModelDefinition:
        row = self._fetch(orm.ModelRow, model.id)
        model.updated_at = utcnow()
        orm.model_to_row(model, row)
        self.session.flush()
        return model

    def delete(self, model_id: str) -> None:
        row = self._fetch(orm.ModelRow, model_id)
        if row:
            self.session.delete(row)
            self.session.flush()

    # -- versions ----------------------------------------------------------
    def add_version(self, version: ModelVersion) -> ModelVersion:
        self.session.add(self._stamp(orm.version_to_row(version)))
        self.session.flush()
        return version

    def get_version(self, version_id: str) -> ModelVersion | None:
        row = self._fetch(orm.ModelVersionRow, version_id)
        return orm.version_to_entity(row) if row else None

    def list_versions(self, model_id: str) -> list[ModelVersion]:
        rows = self.session.scalars(
            self._scoped(select(orm.ModelVersionRow), orm.ModelVersionRow)
            .where(orm.ModelVersionRow.model_id == model_id)
            .order_by(orm.ModelVersionRow.version.desc())
        ).all()
        return [orm.version_to_entity(r) for r in rows]

    def next_version_number(self, model_id: str) -> int:
        current = self.session.scalar(
            select(func.max(orm.ModelVersionRow.version)).where(
                orm.ModelVersionRow.model_id == model_id
            )
        )
        return int(current or 0) + 1
