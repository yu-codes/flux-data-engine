"""Request/response models for the data API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: str
    connection: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class SourceOut(BaseModel):
    id: str
    name: str
    type: str
    connection: dict[str, Any]
    description: str
    created_at: datetime
    #  Where this is filed. Null means shared: it shows under every project
    #  rather than none, which is what the library relies on.
    project_id: str | None = None


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_id: str
    description: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class DatasetVersionOut(BaseModel):
    id: str
    dataset_id: str
    version: int
    row_count: int
    column_count: int
    schema_id: str | None
    storage_uri: str
    lineage: dict[str, Any]
    created_at: datetime


class DatasetOut(BaseModel):
    id: str
    name: str
    origin: str
    source_id: str | None
    description: str
    tags: list[str]
    current_version_id: str | None
    created_at: datetime
    updated_at: datetime
    #  Where this is filed. Null means shared: it shows under every project
    #  rather than none, which is what the library relies on.
    project_id: str | None = None


class DatasetDetailOut(DatasetOut):
    versions: list[DatasetVersionOut] = Field(default_factory=list)
    schema_fields: list[dict[str, Any]] = Field(default_factory=list)


class SchemaOut(BaseModel):
    id: str
    name: str
    description: str
    fields: list[dict[str, Any]]
    created_at: datetime


class PreviewOut(BaseModel):
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    row_count: int = 0
    version_id: str | None = None
    version: int | None = None
