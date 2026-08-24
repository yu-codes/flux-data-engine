"""Result application service: persist, read back and materialise results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.modules.data.application.services import DatasetService
from app.modules.data.domain.entities import DatasetOrigin
from app.shared.errors import NotFoundError
from app.shared.storage import ObjectStore
from app.shared.tabular import Table

from ..domain.entities import (
    INLINE_PAYLOAD_MAX_BYTES,
    Result,
    ResultPayload,
)
from ..domain.ports import ResultRepository

PREVIEW_ROWS = 200


class ResultService:
    def __init__(
        self,
        *,
        repository: ResultRepository,
        store: ObjectStore,
        datasets: DatasetService,
    ):
        self.repository = repository
        self.store = store
        self.datasets = datasets

    # -- reads -------------------------------------------------------------
    def get(self, result_id: str) -> Result:
        result = self.repository.get(result_id)
        if not result:
            raise NotFoundError(f"result '{result_id}' not found")
        return result

    def list(self, **filters) -> list[Result]:
        return self.repository.list(**filters)

    def for_execution(self, execution_id: str) -> Result | None:
        return self.repository.get_by_execution(execution_id)

    def read_table(self, result: Result) -> Table | None:
        """The table a result carries, wherever it was put.

        A materialised result reads from its dataset version; a checkpoint
        reads from its Parquet. Callers that need to chain results together -
        a pipeline, most obviously - should not have to know which happened.
        """
        if result.dataset_version_id:
            return self.datasets.read_table(result.dataset_version_id)
        if result.payload_uri and result.payload_uri.endswith(".parquet"):
            return Table.from_parquet(self.store.local_path(result.payload_uri))
        return None

    def _store_table(self, execution_id: str, table: Table) -> str:
        """Write a table to the object store and return its uri."""
        import tempfile

        with tempfile.TemporaryDirectory(prefix="flux-checkpoint-") as workdir:
            local = table.write_parquet(Path(workdir) / "checkpoint.parquet")
            return self.store.put_file(f"results/{execution_id}/checkpoint.parquet", local)

    def read_payload(self, result_id: str, *, limit: int = PREVIEW_ROWS) -> Any:
        """Return the substance of a result, whichever way it was stored."""
        result = self.get(result_id)
        if result.dataset_version_id:
            table = self.datasets.read_table(result.dataset_version_id)
            return {"kind": "table", "rows": table.to_rows(limit=limit),
                    "columns": [f.to_dict() for f in table.schema_fields()],
                    "row_count": table.num_rows}
        if result.payload_uri and result.payload_uri.endswith(".parquet"):
            table = Table.from_parquet(self.store.local_path(result.payload_uri))
            return {
                "kind": "table",
                "columns": [f.to_dict() for f in table.schema_fields()],
                "rows": table.to_rows(limit=limit),
                "row_count": table.num_rows,
            }
        if result.payload_uri:
            return self.store.get_json(result.payload_uri)
        return result.inline_payload

    # -- writes ------------------------------------------------------------
    def persist(
        self,
        *,
        execution_id: str,
        payload: ResultPayload,
        metrics: dict[str, Any] | None = None,
        dataset_name_hint: str = "result",
        lineage: dict[str, Any] | None = None,
        materialise_dataset: bool | None = None,
    ) -> Result:
        """Turn a plugin's payload into a stored, first-class Result.

        `materialise_dataset` overrides what the payload asked for. A pipeline
        uses it to say that a step in the middle of a run is working state:
        the table is still kept, as a checkpoint the next step reads, but it
        does not become a Dataset with a name and a place in the catalogue.
        """
        result = Result(
            execution_id=execution_id,
            kind=payload.kind,
            summary=payload.summary or {},
            metrics={**(payload.metrics or {}), **(metrics or {})},
        )

        if payload.table is not None:
            result.row_count = payload.table.num_rows
            wanted = (
                payload.materialise_as_dataset
                if materialise_dataset is None
                else materialise_dataset
            )
            if wanted:
                dataset, version = self.datasets.create_from_table(
                    name=payload.dataset_name or dataset_name_hint,
                    table=payload.table,
                    origin=DatasetOrigin.EXECUTION,
                    description=f"materialised from execution {execution_id}",
                    lineage={"execution_id": execution_id, **(lineage or {})},
                )
                result.dataset_id = dataset.id
                result.dataset_version_id = version.id
            else:
                #  Parquet rather than JSON rows: it is the format the platform
                #  already reads and writes, it is a fraction of the size, and
                #  it comes back as a table instead of as a list of dicts that
                #  has to be rebuilt into one.
                result.payload_uri = self._store_table(execution_id, payload.table)
                result.summary = {
                    **result.summary,
                    "checkpoint": True,
                    "columns": [f.to_dict() for f in payload.table.schema_fields()],
                }
        elif payload.value is not None:
            encoded = json.dumps(payload.value, ensure_ascii=False, default=str)
            if len(encoded.encode("utf-8")) <= INLINE_PAYLOAD_MAX_BYTES:
                result.inline_payload = json.loads(encoded)
            else:
                result.payload_uri = self._store_payload(execution_id, payload.value)

        if payload.artifact_path:
            source = Path(payload.artifact_path)
            if source.exists():
                result.artifact_uri = self.store.put_file(
                    f"results/{execution_id}/{source.name}", source
                )

        return self.repository.add(result)

    def materialise(self, result_id: str, dataset_name: str) -> dict[str, Any]:
        """Promote a stored table payload into a first-class Dataset."""
        result = self.get(result_id)
        if result.dataset_version_id:
            return {"dataset_id": result.dataset_id,
                    "dataset_version_id": result.dataset_version_id,
                    "already_materialised": True}
        payload = self.read_payload(result_id, limit=None)
        if not isinstance(payload, dict) or payload.get("kind") != "table":
            raise NotFoundError("this result has no tabular payload to materialise")
        table = Table.from_rows(payload["rows"])
        dataset, version = self.datasets.create_from_table(
            name=dataset_name,
            table=table,
            origin=DatasetOrigin.EXECUTION,
            description=f"materialised from result {result_id}",
            lineage={"result_id": result_id, "execution_id": result.execution_id},
        )
        result.dataset_id = dataset.id
        result.dataset_version_id = version.id
        self.repository.update(result)
        return {"dataset_id": dataset.id, "dataset_version_id": version.id,
                "already_materialised": False}

    def delete(self, result_id: str) -> None:
        result = self.get(result_id)
        if result.payload_uri:
            self.store.delete(result.payload_uri)
        self.repository.delete(result_id)

    # -- internals ---------------------------------------------------------
    def _store_payload(self, execution_id: str, payload: Any) -> str:
        return self.store.put_json(f"results/{execution_id}/payload.json", payload)
