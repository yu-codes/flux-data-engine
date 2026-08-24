"""Object storage port and the local-filesystem backend.

Datasets, result payloads and model artifacts are addressed by URI so the
domain never learns where bytes physically live. The local backend uses
``file://`` URIs; an S3/MinIO backend registers here without any caller change.
"""

from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ObjectStore(ABC):
    """Content-addressed blob storage used by every module."""

    @abstractmethod
    def put_bytes(self, key: str, payload: bytes) -> str:
        """Store raw bytes; returns the URI."""

    @abstractmethod
    def get_bytes(self, uri: str) -> bytes:
        """Read raw bytes back."""

    @abstractmethod
    def put_file(self, key: str, source: Path) -> str:
        """Move/copy a local file into the store; returns the URI."""

    @abstractmethod
    def local_path(self, uri: str) -> Path:
        """A readable local path for the object (may be a cached copy)."""

    @abstractmethod
    def delete(self, uri: str) -> None:
        """Remove the object; missing objects are not an error."""

    @abstractmethod
    def exists(self, uri: str) -> bool:
        """Whether the object is present."""

    # -- convenience shared by all backends --------------------------------
    def put_json(self, key: str, payload: Any) -> str:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return self.put_bytes(key, raw)

    def get_json(self, uri: str) -> Any:
        return json.loads(self.get_bytes(uri).decode("utf-8"))


class LocalObjectStore(ObjectStore):
    """Filesystem-backed store rooted at a single directory."""

    scheme = "file"

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- uri helpers -------------------------------------------------------
    def _uri(self, key: str) -> str:
        return f"{self.scheme}://{key.lstrip('/')}"

    def _path(self, uri: str) -> Path:
        key = uri.split("://", 1)[1] if "://" in uri else uri
        resolved = (self.root / key).resolve()
        if not str(resolved).startswith(str(self.root)):
            raise ValueError(f"object key escapes the store root: {key}")
        return resolved

    # -- ObjectStore -------------------------------------------------------
    def put_bytes(self, key: str, payload: bytes) -> str:
        uri = self._uri(key)
        target = self._path(uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return uri

    def get_bytes(self, uri: str) -> bytes:
        return self._path(uri).read_bytes()

    def put_file(self, key: str, source: Path) -> str:
        uri = self._uri(key)
        target = self._path(uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        if Path(source).resolve() != target:
            shutil.copy2(source, target)
        return uri

    def local_path(self, uri: str) -> Path:
        return self._path(uri)

    def delete(self, uri: str) -> None:
        path = self._path(uri)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    def exists(self, uri: str) -> bool:
        return self._path(uri).exists()


def store_from_settings(settings) -> ObjectStore:
    """Build the configured store.

    Takes the settings object rather than importing it, so `shared` keeps
    pointing at nothing above itself while callers in `core` and in
    `infrastructure` still get the same store without repeating the wiring.
    """
    return create_object_store(
        backend=settings.storage_backend,
        local_root=settings.storage_root,
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        cache_dir=settings.storage_root / "cache",
    )


def create_object_store(
    *,
    backend: str,
    local_root: Path | str,
    bucket: str = "",
    endpoint_url: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str = "us-east-1",
    cache_dir: Path | str | None = None,
) -> ObjectStore:
    """Build the configured backend. The only place a backend is chosen."""
    if backend == "local":
        return LocalObjectStore(local_root)
    if backend == "s3":
        from .s3_storage import S3ObjectStore

        return S3ObjectStore(
            bucket=bucket,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            cache_dir=cache_dir or Path(local_root) / "s3-cache",
        )
    raise ValueError(f"unknown storage backend '{backend}' (use 'local' or 's3')")
