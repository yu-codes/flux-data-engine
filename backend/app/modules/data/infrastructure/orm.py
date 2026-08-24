"""SQLAlchemy mapping for the data module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.contracts import FieldSpec
from app.shared.ids import utcnow

from ..domain.entities import (
    DataSchema,
    Dataset,
    DatasetOrigin,
    DatasetVersion,
    Source,
    SourceType,
)


class SourceRow(Base):
    __tablename__ = "sources"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    connection: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchemaRow(Base):
    __tablename__ = "data_schemas"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetRow(Base):
    __tablename__ = "datasets"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(32), default=DatasetOrigin.SOURCE.value)
    source_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    current_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DatasetVersionRow(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version", name="uq_dataset_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    schema_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("data_schemas.id", ondelete="SET NULL"), nullable=True
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    column_count: Mapped[int] = mapped_column(Integer, default=0)
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# mappers


def source_to_entity(row: SourceRow) -> Source:
    return Source(
        id=row.id,
        name=row.name,
        type=SourceType(row.type),
        connection=row.connection or {},
        description=row.description or "",
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def source_to_row(entity: Source, row: SourceRow | None = None) -> SourceRow:
    row = row or SourceRow(id=entity.id)
    row.name = entity.name
    row.type = entity.type.value
    row.connection = entity.connection
    row.description = entity.description
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row


def schema_to_entity(row: SchemaRow) -> DataSchema:
    return DataSchema(
        id=row.id,
        name=row.name,
        description=row.description or "",
        fields=[FieldSpec.from_dict(f) for f in (row.fields or [])],
        created_at=row.created_at,
    )


def schema_to_row(entity: DataSchema, row: SchemaRow | None = None) -> SchemaRow:
    row = row or SchemaRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.fields = [f.to_dict() for f in entity.fields]
    row.created_at = entity.created_at
    return row


def dataset_to_entity(row: DatasetRow) -> Dataset:
    return Dataset(
        id=row.id,
        name=row.name,
        origin=DatasetOrigin(row.origin),
        source_id=row.source_id,
        description=row.description or "",
        tags=list(row.tags or []),
        current_version_id=row.current_version_id,
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def dataset_to_row(entity: Dataset, row: DatasetRow | None = None) -> DatasetRow:
    row = row or DatasetRow(id=entity.id)
    row.name = entity.name
    row.origin = entity.origin.value
    row.source_id = entity.source_id
    row.description = entity.description
    row.tags = entity.tags
    row.current_version_id = entity.current_version_id
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row


def version_to_entity(row: DatasetVersionRow) -> DatasetVersion:
    return DatasetVersion(
        id=row.id,
        dataset_id=row.dataset_id,
        version=row.version,
        storage_uri=row.storage_uri,
        schema_id=row.schema_id,
        row_count=row.row_count,
        column_count=row.column_count,
        lineage=row.lineage or {},
        created_at=row.created_at,
    )


def version_to_row(entity: DatasetVersion) -> DatasetVersionRow:
    return DatasetVersionRow(
        id=entity.id,
        dataset_id=entity.dataset_id,
        version=entity.version,
        storage_uri=entity.storage_uri,
        schema_id=entity.schema_id,
        row_count=entity.row_count,
        column_count=entity.column_count,
        lineage=entity.lineage,
        created_at=entity.created_at,
    )
