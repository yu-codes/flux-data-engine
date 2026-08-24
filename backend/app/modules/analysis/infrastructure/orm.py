"""SQLAlchemy mapping for the analysis module."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.shared.ids import utcnow

from ..domain.entities import ChartSpec, Dashboard, DashboardTile, Visualization


class VisualizationRow(Base):
    __tablename__ = "visualizations"

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
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    dataset_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DashboardRow(Base):
    __tablename__ = "dashboards"

    #  Which workspace this belongs to, and who made it. Names are unique
    #  within a workspace rather than across the installation, and "who
    #  changed this" stops being a question only the audit log can answer.
    workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tiles: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def visualization_to_entity(row: VisualizationRow) -> Visualization:
    return Visualization(
        id=row.id,
        name=row.name,
        description=row.description or "",
        spec=ChartSpec.from_dict(row.spec),
        dataset_id=row.dataset_id,
        dataset_version_id=row.dataset_version_id,
        result_id=row.result_id,
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def visualization_to_row(
    entity: Visualization, row: VisualizationRow | None = None
) -> VisualizationRow:
    row = row or VisualizationRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.spec = entity.spec.to_dict()
    row.dataset_id = entity.dataset_id
    row.dataset_version_id = entity.dataset_version_id
    row.result_id = entity.result_id
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row


def dashboard_to_entity(row: DashboardRow) -> Dashboard:
    return Dashboard(
        id=row.id,
        name=row.name,
        description=row.description or "",
        tiles=[DashboardTile.from_dict(t) for t in (row.tiles or [])],
        created_at=row.created_at,
        created_by=row.created_by,
        workspace_id=row.workspace_id,
        updated_at=row.updated_at,
    )


def dashboard_to_row(entity: Dashboard, row: DashboardRow | None = None) -> DashboardRow:
    row = row or DashboardRow(id=entity.id)
    row.name = entity.name
    row.description = entity.description
    row.tiles = [t.to_dict() for t in entity.tiles]
    row.created_at = entity.created_at
    row.updated_at = entity.updated_at
    #  Never cleared on update: the creator does not change.
    if entity.created_by:
        row.created_by = entity.created_by
    return row
