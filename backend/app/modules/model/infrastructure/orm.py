"""SQLAlchemy mapping for the model module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.contracts import Contract
from app.shared.ids import utcnow

from ..domain.entities import (
    ModelDefinition,
    ModelStatus,
    ModelType,
    ModelVersion,
    RuntimeKind,
)


class ModelRow(Base):
    __tablename__ = "models"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime: Mapped[str] = mapped_column(String(32), nullable=False)
    #  Indexed because the library listing filters on it on every page load.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active", index=True
    )
    input_contract: Mapped[dict] = mapped_column(JSON, default=dict)
    parameter_contract: Mapped[dict] = mapped_column(JSON, default=dict)
    output_contract: Mapped[dict] = mapped_column(JSON, default=dict)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    current_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelVersionRow(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_execution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)




def model_to_entity(row: ModelRow) -> ModelDefinition:
    return ModelDefinition(
        id=row.id,
        name=row.name,
        slug=row.slug,
        description=row.description or "",
        type=ModelType(row.type),
        status=ModelStatus(row.status or "active"),
        provider=row.provider,
        runtime=RuntimeKind(row.runtime),
        input_contract=Contract.from_dict(row.input_contract),
        parameter_contract=Contract.from_dict(row.parameter_contract),
        output_contract=Contract.from_dict(row.output_contract),
        configuration=row.configuration or {},
        metadata=row.meta or {},
        lineage=row.lineage or {},
        tags=list(row.tags or []),
        current_version_id=row.current_version_id,
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def model_to_row(entity: ModelDefinition, row: ModelRow | None = None) -> ModelRow:
    row = row or ModelRow(id=entity.id)
    row.name = entity.name
    row.slug = entity.slug
    row.description = entity.description
    row.type = entity.type.value
    row.status = entity.status.value
    row.provider = entity.provider
    row.runtime = entity.runtime.value
    row.input_contract = entity.input_contract.to_dict()
    row.parameter_contract = entity.parameter_contract.to_dict()
    row.output_contract = entity.output_contract.to_dict()
    row.configuration = entity.configuration
    row.meta = entity.metadata
    row.lineage = entity.lineage
    row.tags = entity.tags
    row.current_version_id = entity.current_version_id
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row


def version_to_entity(row: ModelVersionRow) -> ModelVersion:
    return ModelVersion(
        id=row.id,
        model_id=row.model_id,
        version=row.version,
        definition_snapshot=row.definition_snapshot or {},
        parameters=row.parameters or {},
        artifact_uri=row.artifact_uri,
        metrics=row.metrics or {},
        created_by_execution_id=row.created_by_execution_id,
        notes=row.notes or "",
        created_at=row.created_at,
    )


def version_to_row(entity: ModelVersion) -> ModelVersionRow:
    return ModelVersionRow(
        id=entity.id,
        model_id=entity.model_id,
        version=entity.version,
        definition_snapshot=entity.definition_snapshot,
        parameters=entity.parameters,
        artifact_uri=entity.artifact_uri,
        metrics=entity.metrics,
        created_by_execution_id=entity.created_by_execution_id,
        notes=entity.notes,
        created_at=entity.created_at,
    )
