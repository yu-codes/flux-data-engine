"""SQLAlchemy repositories for the data module."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.shared.ids import utcnow
from app.shared.scoping import WorkspaceScope, WorkspaceScoped

from ..domain.entities import DataSchema, Dataset, DatasetVersion, Source
from . import orm


class SqlSourceRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, source: Source) -> Source:
        self.session.add(self._stamp(orm.source_to_row(source)))
        self.session.flush()
        return source

    def get(self, source_id: str) -> Source | None:
        row = self._fetch(orm.SourceRow, source_id)
        return orm.source_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Source | None:
        row = self.session.scalar(
            self._scoped(select(orm.SourceRow), orm.SourceRow)
            .where(orm.SourceRow.name == name)
        )
        return orm.source_to_entity(row) if row else None

    def list(self) -> list[Source]:
        rows = self.session.scalars(
            self._scoped(select(orm.SourceRow), orm.SourceRow)
            .order_by(orm.SourceRow.created_at.desc())
        ).all()
        return [orm.source_to_entity(r) for r in rows]

    def update(self, source: Source) -> Source:
        row = self._fetch(orm.SourceRow, source.id)
        source.updated_at = utcnow()
        orm.source_to_row(source, row)
        self.session.flush()
        return source

    def delete(self, source_id: str) -> None:
        row = self._fetch(orm.SourceRow, source_id)
        if row:
            self.session.delete(row)
            self.session.flush()


class SqlSchemaRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    def add(self, schema: DataSchema) -> DataSchema:
        self.session.add(self._stamp(orm.schema_to_row(schema)))
        self.session.flush()
        return schema

    def get(self, schema_id: str) -> DataSchema | None:
        row = self._fetch(orm.SchemaRow, schema_id)
        return orm.schema_to_entity(row) if row else None

    def list(self) -> list[DataSchema]:
        rows = self.session.scalars(
            self._scoped(select(orm.SchemaRow), orm.SchemaRow)
            .order_by(orm.SchemaRow.created_at.desc())
        ).all()
        return [orm.schema_to_entity(r) for r in rows]

    def delete(self, schema_id: str) -> None:
        row = self._fetch(orm.SchemaRow, schema_id)
        if row:
            self.session.delete(row)
            self.session.flush()


class SqlDatasetRepository(WorkspaceScoped):
    def __init__(self, session: Session, scope: WorkspaceScope | None = None):
        self.session = session
        #  Unscoped is the worker's case: it works across workspaces
        #  because the queue it reads from is shared.
        self.scope = scope or WorkspaceScope.unscoped()

    # -- datasets ----------------------------------------------------------
    def add(self, dataset: Dataset) -> Dataset:
        self.session.add(self._stamp(orm.dataset_to_row(dataset)))
        self.session.flush()
        return dataset

    def get(self, dataset_id: str) -> Dataset | None:
        row = self._fetch(orm.DatasetRow, dataset_id)
        return orm.dataset_to_entity(row) if row else None

    def get_by_name(self, name: str) -> Dataset | None:
        row = self.session.scalar(
            self._scoped(select(orm.DatasetRow), orm.DatasetRow)
            .where(orm.DatasetRow.name == name)
        )
        return orm.dataset_to_entity(row) if row else None

    def list(
        self, *, origins: list[str] | None = None, search: str | None = None
    ) -> list[Dataset]:
        stmt = self._scoped(select(orm.DatasetRow), orm.DatasetRow)
        if origins:
            stmt = stmt.where(orm.DatasetRow.origin.in_(origins))
        if search:
            like = f"%{search.lower()}%"
            stmt = stmt.where(
                func.lower(orm.DatasetRow.name).like(like)
                | func.lower(orm.DatasetRow.description).like(like)
            )
        rows = self.session.scalars(
            stmt.order_by(orm.DatasetRow.created_at.desc())
        ).all()
        return [orm.dataset_to_entity(r) for r in rows]

    def update(self, dataset: Dataset) -> Dataset:
        row = self._fetch(orm.DatasetRow, dataset.id)
        dataset.updated_at = utcnow()
        orm.dataset_to_row(dataset, row)
        self.session.flush()
        return dataset

    def delete(self, dataset_id: str) -> None:
        row = self._fetch(orm.DatasetRow, dataset_id)
        if row:
            self.session.delete(row)
            self.session.flush()

    # -- versions ----------------------------------------------------------
    def add_version(self, version: DatasetVersion) -> DatasetVersion:
        self.session.add(self._stamp(orm.version_to_row(version)))
        self.session.flush()
        return version

    def get_version(self, version_id: str) -> DatasetVersion | None:
        row = self._fetch(orm.DatasetVersionRow, version_id)
        return orm.version_to_entity(row) if row else None

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        rows = self.session.scalars(
            self._scoped(select(orm.DatasetVersionRow), orm.DatasetVersionRow)
            .where(orm.DatasetVersionRow.dataset_id == dataset_id)
            .order_by(orm.DatasetVersionRow.version.desc())
        ).all()
        return [orm.version_to_entity(r) for r in rows]

    def next_version_number(self, dataset_id: str) -> int:
        current = self.session.scalar(
            select(func.max(orm.DatasetVersionRow.version)).where(
                orm.DatasetVersionRow.dataset_id == dataset_id
            )
        )
        return int(current or 0) + 1
