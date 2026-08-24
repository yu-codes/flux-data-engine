"""Source readers: external format in, Arrow Table out.

Adding a format means adding a reader here and registering it. Nothing outside
this file knows that CSV or Excel exist.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.shared.errors import NotFoundError, UnsupportedError, ValidationError
from app.shared.outbound import MAX_REDIRECTS, check_url
from app.shared.storage import store_from_settings
from app.shared.tabular import Table

from ..domain.entities import Source, SourceType
from .sql_guard import assert_read_only, safe_table_name

MAX_REST_ROWS = 100_000
REST_TIMEOUT_SECONDS = 30
#  A response the platform will hold in memory before parsing it.
MAX_REST_BYTES = 64 * 1024 * 1024


def resolve_path(raw_path: str) -> Path:
    """Resolve a configured path, refusing anything outside the allowed roots.

    All external input is validated here: a source may only address files under
    the project's data or storage roots, never arbitrary filesystem locations.
    """
    settings = get_settings()
    roots = [settings.data_root.resolve(), settings.storage_root.resolve()]
    candidate = Path(raw_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (settings.data_root / candidate).resolve()
    )
    if not any(str(resolved).startswith(str(root)) for root in roots):
        raise ValidationError(
            f"path '{raw_path}' is outside the allowed data roots",
            details={"allowed_roots": [str(r) for r in roots]},
        )
    if not resolved.exists():
        raise ValidationError(f"file not found: {raw_path}")
    return resolved


def _dig(payload: Any, dotted: str | None) -> Any:
    """Follow a dotted path into nested JSON, e.g. ``result.items``."""
    if not dotted:
        return payload
    current = payload
    for part in dotted.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            raise ValidationError(f"records_path '{dotted}' does not match the payload")
    return current


def _rows_from_json(payload: Any, records_path: str | None) -> list[dict]:
    records = _dig(payload, records_path)
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ValidationError("expected a list of records after applying records_path")
    return [r for r in records if isinstance(r, dict)]


def _flatten(rows: Iterable[dict], limit: int | None) -> list[dict]:
    """Keep nested values as JSON so Arrow can hold them without exploding."""
    out: list[dict] = []
    for index, row in enumerate(rows):
        if limit is not None and index >= limit:
            break
        out.append(
            {
                key: (json.dumps(value, ensure_ascii=False)
                      if isinstance(value, (dict, list))
                      else value)
                for key, value in row.items()
            }
        )
    return out


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------
class CsvReader:
    source_type = SourceType.CSV

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        path = resolve_path(cfg["path"])
        frame = pd.read_csv(
            path,
            sep=cfg.get("delimiter", ","),
            encoding=cfg.get("encoding", "utf-8"),
            nrows=cfg.get("limit"),
        )
        return Table.from_pandas(frame)

    def describe(self, source: Source) -> dict[str, Any]:
        path = resolve_path(source.connection["path"])
        return {"path": str(path), "size_bytes": path.stat().st_size}


class ExcelReader:
    source_type = SourceType.EXCEL

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        path = resolve_path(cfg["path"])
        frame = pd.read_excel(path, sheet_name=cfg.get("sheet", 0))
        if isinstance(frame, dict):  # sheet_name=None returns every sheet
            frame = next(iter(frame.values()))
        if cfg.get("limit"):
            frame = frame.head(int(cfg["limit"]))
        return Table.from_pandas(frame)

    def describe(self, source: Source) -> dict[str, Any]:
        path = resolve_path(source.connection["path"])
        return {"path": str(path), "sheets": pd.ExcelFile(path).sheet_names}


class JsonReader:
    source_type = SourceType.JSON

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        path = resolve_path(cfg["path"])
        with open(path, encoding=cfg.get("encoding", "utf-8")) as handle:
            payload = json.load(handle)
        rows = _rows_from_json(payload, cfg.get("records_path"))
        return Table.from_rows(_flatten(rows, cfg.get("limit")))

    def describe(self, source: Source) -> dict[str, Any]:
        path = resolve_path(source.connection["path"])
        return {"path": str(path), "size_bytes": path.stat().st_size}


class NdjsonReader:
    source_type = SourceType.NDJSON

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        path = resolve_path(cfg["path"])
        limit = cfg.get("limit")
        rows: list[dict] = []
        with open(path, encoding=cfg.get("encoding", "utf-8")) as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
                if limit and len(rows) >= int(limit):
                    break
        return Table.from_rows(_flatten(rows, None))

    def describe(self, source: Source) -> dict[str, Any]:
        path = resolve_path(source.connection["path"])
        return {"path": str(path), "size_bytes": path.stat().st_size}


class ParquetReader:
    source_type = SourceType.PARQUET

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        path = resolve_path(cfg["path"])
        table = pq.read_table(str(path))
        if cfg.get("limit"):
            table = table.slice(0, int(cfg["limit"]))
        return Table(table)

    def describe(self, source: Source) -> dict[str, Any]:
        path = resolve_path(source.connection["path"])
        meta = pq.read_metadata(str(path))
        return {"path": str(path), "rows": meta.num_rows, "columns": meta.num_columns}


class DatabaseReader:
    """Read one table, or one read-only query, from an external database.

    Two gates, because two different things can go wrong. The host is checked
    against the outbound policy so this cannot be used to reach a database the
    platform merely happens to sit next to; the statement is checked so that
    "register a data source" does not quietly mean "run arbitrary SQL".
    """

    source_type = SourceType.DATABASE

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        url = cfg.get("url")
        if not url:
            raise ValidationError("database source requires a 'url'")
        _check_database_host(url)

        query = cfg.get("query")
        if query:
            query = assert_read_only(query)
        else:
            table_name = cfg.get("table")
            if not table_name:
                raise ValidationError("database source requires 'query' or 'table'")
            query = f"SELECT * FROM {safe_table_name(table_name)}"

        limit = int(cfg.get("limit") or 50_000)
        engine = create_engine(url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                #  A transaction the reader never commits: even if a statement
                #  slipped past the gate, the database is asked to refuse it.
                connection.execution_options(postgresql_readonly=True)
                rows = connection.execute(text(query)).mappings().fetchmany(limit)
        finally:
            engine.dispose()
        return Table.from_rows(_flatten([dict(r) for r in rows], None))

    def describe(self, source: Source) -> dict[str, Any]:
        return {"url": "***", "table": source.connection.get("table")}


class RestApiReader:
    """Fetch JSON from an HTTP endpoint the outbound policy allows.

    Redirects are followed by hand rather than by the HTTP library: a redirect
    is a URL the platform never validated, and "https://example.com" that
    301s to "http://169.254.169.254/latest/meta-data/" is the whole attack.
    """

    source_type = SourceType.REST_API

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        url = cfg.get("url")
        if not url:
            raise ValidationError("rest_api source requires a 'url'")

        policy = get_settings().network_policy
        method = str(cfg.get("method", "GET")).upper()
        seen = 0
        while True:
            check_url(str(url), policy)
            response = requests.request(
                method=method,
                url=url,
                headers=cfg.get("headers") or {},
                params=cfg.get("params") or {},
                json=cfg.get("body"),
                timeout=REST_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            if response.is_redirect or response.is_permanent_redirect:
                seen += 1
                if seen > MAX_REDIRECTS:
                    raise ValidationError(
                        f"rest_api source followed more than {MAX_REDIRECTS} redirects"
                    )
                url = response.headers.get("location")
                if not url:
                    raise ValidationError("redirect response carried no location")
                url = requests.compat.urljoin(response.url, url)
                response.close()
                continue
            break

        response.raise_for_status()
        payload = _read_capped(response)
        rows = _rows_from_json(payload, cfg.get("records_path"))
        return Table.from_rows(_flatten(rows, cfg.get("limit") or MAX_REST_ROWS))

    def describe(self, source: Source) -> dict[str, Any]:
        return {
            "url": source.connection.get("url"),
            "method": source.connection.get("method", "GET"),
        }


def _check_database_host(url: str) -> None:
    """Apply the outbound policy to a SQLAlchemy URL.

    The scheme is a driver name, not http, so the host is re-wrapped before
    being checked - the question being asked is only "may we reach this host".
    """
    parsed = make_url(url)
    if not parsed.host:
        #  No host means a local file database (sqlite), which reaches nothing.
        return
    port = f":{parsed.port}" if parsed.port else ""
    check_url(f"https://{parsed.host}{port}", get_settings().network_policy)


def _read_capped(response) -> Any:
    """Read a bounded number of bytes, then parse.

    An endpoint that streams forever would otherwise be an out-of-memory
    condition triggered by whoever registered the source.
    """
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(64 * 1024):
        total += len(chunk)
        if total > MAX_REST_BYTES:
            response.close()
            raise ValidationError(
                f"rest_api response exceeds the "
                f"{MAX_REST_BYTES // (1024 * 1024)} MB limit"
            )
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        return json.loads(body.decode(response.encoding or "utf-8", errors="replace"))
    except ValueError as exc:
        raise ValidationError(f"rest_api response was not JSON: {exc}") from exc


class ObjectStorageReader:
    """Read an object out of the platform's object store and parse it.

    The format is decided by the key's extension and handed to the reader that
    already knows it, so this adds a *location*, not a format - which is the
    whole reason the enum separates "where the bytes are" from "what shape they
    are in".
    """

    source_type = SourceType.OBJECT_STORAGE

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        uri = cfg.get("uri") or cfg.get("key")
        if not uri:
            raise ValidationError("object_storage source requires a 'uri' or 'key'")

        store = store_from_settings(get_settings())
        if not store.exists(uri):
            raise NotFoundError(f"no object at '{uri}'")

        suffix = (cfg.get("format") or Path(str(uri)).suffix.lstrip(".")).lower()
        source_type = _OBJECT_FORMATS.get(suffix)
        if source_type is None:
            raise ValidationError(
                f"cannot tell what format '{uri}' is in",
                details={"supported": sorted(_OBJECT_FORMATS)},
            )

        #  Hand the delegate a real file: local_path caches an S3 object once
        #  rather than every reader learning to stream.
        path = store.local_path(uri)
        delegate = get_reader(source_type)
        stand_in = Source(
            name=source.name,
            type=source_type,
            connection={"path": str(path)},
        )
        return delegate.read(stand_in, options)

    def describe(self, source: Source) -> dict[str, Any]:
        uri = source.connection.get("uri") or source.connection.get("key")
        return {"uri": uri}


#  Extension to the reader that handles it. Deliberately the same set the
#  upload path accepts: a file is a file wherever it is stored.
_OBJECT_FORMATS = {
    "csv": SourceType.CSV,
    "tsv": SourceType.CSV,
    "xlsx": SourceType.EXCEL,
    "xls": SourceType.EXCEL,
    "json": SourceType.JSON,
    "ndjson": SourceType.NDJSON,
    "jsonl": SourceType.NDJSON,
    "parquet": SourceType.PARQUET,
}


class InlineReader:
    """Rows carried inside the source definition itself - handy for demos."""

    source_type = SourceType.INLINE

    @staticmethod
    def _rows(connection: dict[str, Any]) -> list[dict]:
        """The rows, or a refusal naming what was wrong with them.

        The connection body is whatever the caller posted, so this is the point
        where it stops being arbitrary JSON: without the check a string here
        reaches `row.items()` and the request fails as a server error rather
        than as the bad input it is.
        """
        rows = connection.get("rows") or []
        if not isinstance(rows, list):
            raise ValidationError(
                "inline 'rows' must be a list of objects, "
                f"got {type(rows).__name__}"
            )
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValidationError(
                    f"inline row {index} must be an object, "
                    f"got {type(row).__name__}"
                )
        return rows

    def read(self, source: Source, options: dict[str, Any] | None = None) -> Table:
        cfg = {**source.connection, **(options or {})}
        return Table.from_rows(_flatten(self._rows(cfg), cfg.get("limit")))

    def describe(self, source: Source) -> dict[str, Any]:
        return {"rows": len(self._rows(source.connection))}


_READERS = {
    reader.source_type: reader
    for reader in (
        CsvReader(),
        ExcelReader(),
        JsonReader(),
        NdjsonReader(),
        ParquetReader(),
        DatabaseReader(),
        RestApiReader(),
        ObjectStorageReader(),
        InlineReader(),
    )
}


def get_reader(source_type: SourceType):
    reader = _READERS.get(source_type)
    if reader is None:
        raise UnsupportedError(
            f"no reader registered for source type '{source_type.value}'"
        )
    return reader


def supported_source_types() -> list[str]:
    return [t.value for t in _READERS]
