"""Data application services: register sources, ingest and version datasets."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, BinaryIO

from app.shared.contracts import FieldSpec
from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import new_id, utcnow
from app.shared.storage import ObjectStore
from app.shared.tabular import Table

from ..domain.entities import (
    DataSchema,
    Dataset,
    DatasetOrigin,
    DatasetVersion,
    Source,
    SourceType,
)
from ..domain.ports import (
    DatasetRepository,
    ReaderRegistry,
    SchemaRepository,
    SourceRepository,
)

PREVIEW_LIMIT = 100


#  Which file extensions the platform will accept, and how big. This is a
#  statement about what the platform ingests, so it lives with the ingestion
#  code rather than in whichever transport happened to carry the bytes.
UPLOAD_SUFFIX_TO_TYPE = {
    ".csv": SourceType.CSV,
    ".tsv": SourceType.CSV,
    ".xlsx": SourceType.EXCEL,
    ".xls": SourceType.EXCEL,
    ".json": SourceType.JSON,
    ".ndjson": SourceType.NDJSON,
    ".jsonl": SourceType.NDJSON,
    ".parquet": SourceType.PARQUET,
}
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


class SourceService:
    def __init__(
        self,
        repository: SourceRepository,
        readers: ReaderRegistry,
        upload_root: Path | None = None,
    ):
        self.repository = repository
        self.readers = readers
        #  Where an uploaded file lands. Injected rather than read from global
        #  settings so the application layer stays free of configuration.
        self.upload_root = upload_root

    def create_from_upload(
        self,
        *,
        stream: BinaryIO,
        filename: str,
        name: str,
    ) -> Source:
        """Land an uploaded file and register it as a Source.

        Streamed and size-capped rather than read whole: an upload is the one
        place a caller chooses how many bytes the platform handles, so the
        limit is enforced while writing and a file that exceeds it leaves
        nothing behind.
        """
        suffix = Path(filename or "").suffix.lower()
        source_type = UPLOAD_SUFFIX_TO_TYPE.get(suffix)
        if source_type is None:
            raise ValidationError(
                f"unsupported upload type '{suffix}'",
                details={"supported": sorted(UPLOAD_SUFFIX_TO_TYPE)},
            )
        if self.upload_root is None:
            raise ValidationError("this deployment has no upload directory configured")

        target_dir = self.upload_root / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{new_id('up')}{suffix}"

        written = 0
        try:
            with target.open("wb") as handle:
                while chunk := stream.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise ValidationError(
                            f"upload exceeds the "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit"
                        )
                    handle.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise

        return self.create(
            name=f"{name} (upload)",
            source_type=source_type.value,
            connection={"path": str(target.relative_to(self.upload_root))},
            description=f"uploaded file {filename}",
        )

    def create(
        self,
        *,
        name: str,
        source_type: str,
        connection: dict[str, Any],
        description: str = "",
    ) -> Source:
        if self.repository.get_by_name(name):
            raise ConflictError(f"a source named '{name}' already exists")
        source = Source(
            name=name,
            type=SourceType(source_type),
            connection=connection or {},
            description=description,
        )
        # Fail fast: a source that cannot be read is not worth storing.
        self.readers(source.type).describe(source)
        return self.repository.add(source)

    def get(self, source_id: str) -> Source:
        source = self.repository.get(source_id)
        if not source:
            raise NotFoundError(f"source '{source_id}' not found")
        return source

    def list(self) -> list[Source]:
        return self.repository.list()

    def describe(self, source_id: str) -> dict[str, Any]:
        source = self.get(source_id)
        return self.readers(source.type).describe(source)

    def preview(self, source_id: str, limit: int = PREVIEW_LIMIT) -> dict[str, Any]:
        source = self.get(source_id)
        table = self.readers(source.type).read(source, {"limit": limit})
        return {
            "columns": [f.to_dict() for f in table.schema_fields()],
            "rows": table.to_rows(limit=limit),
            "row_count": table.num_rows,
        }

    def delete(self, source_id: str) -> None:
        self.get(source_id)
        self.repository.delete(source_id)


class DatasetService:
    """Owns dataset lifecycle: ingest, version, read back, profile."""

    def __init__(
        self,
        *,
        datasets: DatasetRepository,
        schemas: SchemaRepository,
        sources: SourceRepository,
        store: ObjectStore,
        readers: ReaderRegistry,
    ):
        self.datasets = datasets
        self.schemas = schemas
        self.sources = sources
        self.store = store
        self.readers = readers

    # -- reads -------------------------------------------------------------
    def get(self, dataset_id: str) -> Dataset:
        dataset = self.datasets.get(dataset_id)
        if not dataset:
            raise NotFoundError(f"dataset '{dataset_id}' not found")
        return dataset

    def list(self, **filters) -> list[Dataset]:
        return self.datasets.list(**filters)

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        self.get(dataset_id)
        return self.datasets.list_versions(dataset_id)

    def get_version(self, version_id: str) -> DatasetVersion:
        version = self.datasets.get_version(version_id)
        if not version:
            raise NotFoundError(f"dataset version '{version_id}' not found")
        return version

    def current_version(self, dataset_id: str) -> DatasetVersion:
        dataset = self.get(dataset_id)
        if not dataset.current_version_id:
            raise NotFoundError(f"dataset '{dataset_id}' has no materialised version")
        return self.get_version(dataset.current_version_id)

    def get_schema(self, schema_id: str) -> DataSchema:
        schema = self.schemas.get(schema_id)
        if not schema:
            raise NotFoundError(f"schema '{schema_id}' not found")
        return schema

    def schema_of_version(self, version_id: str) -> DataSchema | None:
        version = self.get_version(version_id)
        return self.schemas.get(version.schema_id) if version.schema_id else None

    def read_table(self, version_id: str, columns: list[str] | None = None) -> Table:
        """Read a version, optionally only some of its columns.

        Parquet is columnar, so a projection is not a filter applied after
        reading - the bytes for the other columns are never touched. Callers
        that know which columns they need should say so.
        """
        version = self.get_version(version_id)
        return Table.from_parquet(
            self.store.local_path(version.storage_uri), columns=columns
        )

    def schema_fields(self, version_id: str) -> list[FieldSpec]:
        """The columns of a version, without reading its rows.

        Falls back to the file's own schema for a version that predates schema
        registration, which still costs only the Parquet footer.
        """
        version = self.get_version(version_id)
        if version.schema_id:
            return self.get_schema(version.schema_id).fields
        return Table.from_parquet(
            self.store.local_path(version.storage_uri), columns=[]
        ).schema_fields()

    def read_current_table(self, dataset_id: str) -> Table:
        return self.read_table(self.current_version(dataset_id).id)

    def preview(
        self, version_id: str, *, limit: int = PREVIEW_LIMIT, offset: int = 0
    ) -> dict[str, Any]:
        version = self.get_version(version_id)
        table = self.read_table(version_id)
        return {
            "version_id": version.id,
            "version": version.version,
            "row_count": version.row_count,
            "columns": [f.to_dict() for f in table.schema_fields()],
            "rows": table.to_rows(limit=limit, offset=offset),
        }

    # -- writes ------------------------------------------------------------
    def create_from_source(
        self,
        *,
        source_id: str,
        name: str,
        description: str = "",
        options: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> tuple[Dataset, DatasetVersion]:
        source = self.sources.get(source_id)
        if not source:
            raise NotFoundError(f"source '{source_id}' not found")
        if self.datasets.get_by_name(name):
            raise ConflictError(f"a dataset named '{name}' already exists")

        table = self.readers(source.type).read(source, options)
        dataset = self.datasets.add(
            Dataset(
                name=name,
                origin=DatasetOrigin.SOURCE,
                source_id=source.id,
                description=description,
                tags=tags or [],
            )
        )
        version = self._materialise(
            dataset,
            table,
            lineage={"source_id": source.id, "source_type": source.type.value,
                     "options": options or {}},
        )
        return dataset, version

    def refresh(
        self, dataset_id: str, options: dict[str, Any] | None = None
    ) -> DatasetVersion:
        """Re-read the origin source and append a new immutable version."""
        dataset = self.get(dataset_id)
        if not dataset.source_id:
            raise ValidationError(f"dataset '{dataset.name}' is not backed by a source")
        source = self.sources.get(dataset.source_id)
        if not source:
            raise NotFoundError(f"source '{dataset.source_id}' no longer exists")
        table = self.readers(source.type).read(source, options)
        return self._materialise(
            dataset,
            table,
            lineage={"source_id": source.id, "refreshed": True, "options": options or {}},
        )

    def create_from_table(
        self,
        *,
        name: str,
        table: Table,
        origin: DatasetOrigin = DatasetOrigin.EXECUTION,
        description: str = "",
        lineage: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> tuple[Dataset, DatasetVersion]:
        """Materialise an in-memory Table as a dataset (used by Results)."""
        unique_name = self._unique_name(name)
        dataset = self.datasets.add(
            Dataset(
                name=unique_name,
                origin=origin,
                description=description,
                tags=tags or [],
            )
        )
        version = self._materialise(dataset, table, lineage=lineage or {})
        return dataset, version

    def delete(self, dataset_id: str) -> None:
        dataset = self.get(dataset_id)
        for version in self.datasets.list_versions(dataset_id):
            self.store.delete(version.storage_uri)
        self.datasets.delete(dataset.id)

    # -- internals ---------------------------------------------------------
    def _unique_name(self, name: str) -> str:
        if not self.datasets.get_by_name(name):
            return name
        return f"{name} ({new_id('v')[2:8]})"

    def _materialise(
        self, dataset: Dataset, table: Table, *, lineage: dict[str, Any]
    ) -> DatasetVersion:
        """Write the table as Parquet and register an immutable version."""
        number = self.datasets.next_version_number(dataset.id)
        version_id = new_id("dsv")
        key = f"datasets/{dataset.id}/v{number}/{version_id}.parquet"

        #  Parquet has to be written to a real file first. Staging it in a
        #  temporary directory keeps this independent of which backend the
        #  object store happens to be.
        with tempfile.TemporaryDirectory(prefix="flux-dataset-") as workdir:
            staged = Path(workdir) / f"{version_id}.parquet"
            table.write_parquet(staged)
            uri = self.store.put_file(key, staged)

        schema = self.schemas.add(
            DataSchema(
                name=f"{dataset.name} v{number}",
                fields=table.schema_fields(),
                description=f"inferred from {dataset.name} version {number}",
            )
        )
        version = self.datasets.add_version(
            DatasetVersion(
                id=version_id,
                dataset_id=dataset.id,
                version=number,
                storage_uri=uri,
                schema_id=schema.id,
                row_count=table.num_rows,
                column_count=table.num_columns,
                lineage=lineage,
            )
        )
        dataset.current_version_id = version.id
        dataset.updated_at = utcnow()
        self.datasets.update(dataset)
        return version


def profile_table(table: Table, *, max_categories: int = 12) -> list[dict[str, Any]]:
    """Per-column statistics used by the Explore page and data-quality checks.

    Computed column by column in Arrow. The previous version called `to_rows()`
    first and then walked every value of every column in Python, so profiling a
    forty-column table cost forty full passes over a list of dicts that had
    itself just been built - which is why opening Explore on anything real was
    the slowest thing in the product.
    """
    profiles: list[dict[str, Any]] = []
    total = table.num_rows
    for spec in table.schema_fields():
        profiles.append(
            {
                "name": spec.name,
                "type": spec.type.value,
                "count": total,
                **table.column_profile(spec.name, max_categories=max_categories),
            }
        )
    return profiles


def _hashable(value: Any) -> Any:
    return value if isinstance(value, (str, int, float, bool, type(None))) else str(value)


def _is_nan(value: Any) -> bool:
    import math

    return isinstance(value, float) and math.isnan(value)


def _stddev(values: list[float]) -> float:
    import math

    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def schema_fields_from_payload(raw_fields: list[dict]) -> list[FieldSpec]:
    return [FieldSpec.from_dict(f) for f in raw_fields]
