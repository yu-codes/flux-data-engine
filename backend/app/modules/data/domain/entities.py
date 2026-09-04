"""Data domain entities: Source -> Dataset -> DatasetVersion -> Schema.

A Source describes *where* external data comes from. A Dataset is the platform's
normalised, versioned view of it. A DatasetVersion is immutable: reading a
source again produces a new version, never a mutation of an old one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.contracts import FieldSpec
from app.shared.ids import new_id, utcnow


class SourceType(str, Enum):
    """External formats the platform can normalise. Extending this list is the
    only place a new input format touches the domain."""

    CSV = "csv"
    EXCEL = "excel"
    JSON = "json"
    NDJSON = "ndjson"
    PARQUET = "parquet"
    DATABASE = "database"
    REST_API = "rest_api"
    OBJECT_STORAGE = "object_storage"
    INLINE = "inline"


class DatasetOrigin(str, Enum):
    """How a dataset came to exist."""

    SOURCE = "source"          # ingested from a Source
    EXECUTION = "execution"    # materialised from an Execution Result
    UPLOAD = "upload"          # uploaded file
    BUILTIN = "builtin"        # shipped with the platform
    #  Working state: the output of a pipeline step something else consumes.
    #  A terminal step's output stays EXECUTION, because that is the deliverable
    #  the pipeline was built to produce; only the stages on the way are hidden.
    INTERMEDIATE = "intermediate"


@dataclass
class Source:
    """A connection description for external data."""

    name: str
    type: SourceType
    connection: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    id: str = field(default_factory=lambda: new_id("src"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)

    def redacted_connection(self) -> dict[str, Any]:
        """Connection details safe to return over the API."""
        secret_keys = {"password", "secret", "token", "api_key", "access_key"}
        return {
            key: ("***" if key.lower() in secret_keys else value)
            for key, value in self.connection.items()
        }


@dataclass
class DataSchema:
    """A named field set. Owned by a dataset version, reusable as a contract."""

    name: str
    fields: list[FieldSpec] = field(default_factory=list)
    description: str = ""
    id: str = field(default_factory=lambda: new_id("sch"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


@dataclass
class DatasetVersion:
    """An immutable materialisation of a dataset, stored as Parquet."""

    dataset_id: str
    version: int
    storage_uri: str
    schema_id: str | None = None
    row_count: int = 0
    column_count: int = 0
    lineage: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("dsv"))
    created_at: datetime = field(default_factory=utcnow)


@dataclass
class Dataset:
    """A named, versioned table inside the platform."""

    name: str
    origin: DatasetOrigin = DatasetOrigin.SOURCE
    source_id: str | None = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    current_version_id: str | None = None
    id: str = field(default_factory=lambda: new_id("ds"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    #  Which project this is filed under. Null means it is not filed and
    #  shows in every project — a deliberately shared model, or a run the
    #  scheduler made without standing anywhere.
    project_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)
