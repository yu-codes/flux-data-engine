"""S3 / MinIO backend for the ObjectStore port.

Callers never change: they still hand out and receive URIs. Only the scheme
differs — ``s3://bucket/key`` instead of ``file://key``.

Objects that must be read as files (Parquet datasets, model artifacts) are
downloaded into a local cache directory on first use and re-used afterwards,
keyed by the object's ETag so a changed object is re-fetched.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

from .errors import FluxError
from .storage import ObjectStore

logger = logging.getLogger(__name__)


class StorageError(FluxError):
    status_code = 502
    code = "storage_error"


class S3ObjectStore(ObjectStore):
    """Bucket-backed store. Works against AWS S3 and any S3-compatible server."""

    scheme = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        cache_dir: Path | str,
        create_bucket: bool = True,
    ):
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
            raise StorageError(
                "the s3 storage backend needs boto3; install it or set "
                "FLUX_STORAGE_BACKEND=local"
            ) from exc

        self.bucket = bucket
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            #  Path-style addressing is what MinIO expects; it is also valid
            #  against AWS, so one setting covers both.
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        if create_bucket:
            self._ensure_bucket()

    # -- bucket ------------------------------------------------------------
    def _ensure_bucket(self) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status not in (403, 404):
                raise StorageError(f"cannot reach bucket '{self.bucket}': {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"cannot reach the object store: {exc}") from exc

        try:
            self._client.create_bucket(Bucket=self.bucket)
            logger.info("created object storage bucket '%s'", self.bucket)
        except ClientError as exc:
            #  A racing worker may have created it between our check and now.
            if exc.response.get("Error", {}).get("Code") not in (
                "BucketAlreadyOwnedByYou",
                "BucketAlreadyExists",
            ):
                raise StorageError(f"cannot create bucket '{self.bucket}': {exc}") from exc

    # -- uri helpers -------------------------------------------------------
    def _uri(self, key: str) -> str:
        return f"{self.scheme}://{self.bucket}/{key.lstrip('/')}"

    def _key(self, uri: str) -> str:
        if "://" not in uri:
            return uri.lstrip("/")
        _, remainder = uri.split("://", 1)
        bucket, _, key = remainder.partition("/")
        if bucket != self.bucket:
            raise StorageError(
                f"object '{uri}' lives in bucket '{bucket}', "
                f"but this store is bound to '{self.bucket}'"
            )
        return key

    # -- ObjectStore -------------------------------------------------------
    def put_bytes(self, key: str, payload: bytes) -> str:
        self._call(self._client.put_object, Bucket=self.bucket,
                   Key=key.lstrip("/"), Body=payload)
        self._invalidate(key.lstrip("/"))
        return self._uri(key)

    def get_bytes(self, uri: str) -> bytes:
        response = self._call(self._client.get_object, Bucket=self.bucket,
                              Key=self._key(uri))
        return response["Body"].read()

    def put_file(self, key: str, source: Path) -> str:
        self._call(self._client.upload_file, str(source), self.bucket, key.lstrip("/"))
        self._invalidate(key.lstrip("/"))
        return self._uri(key)

    def local_path(self, uri: str) -> Path:
        """Download once, then reuse. The cache is keyed by object identity."""
        key = self._key(uri)
        with self._lock:
            head = self._call(self._client.head_object, Bucket=self.bucket, Key=key)
            etag = str(head.get("ETag", "")).strip('"')
            target = self._cache_path(key, etag)
            if target.exists():
                return target
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".partial")
            self._call(self._client.download_file, self.bucket, key, str(partial))
            partial.replace(target)
            return target

    def delete(self, uri: str) -> None:
        key = self._key(uri)
        try:
            self._call(self._client.delete_object, Bucket=self.bucket, Key=key)
        except StorageError:
            #  Deleting something that is already gone is not an error.
            pass
        self._invalidate(key)

    def exists(self, uri: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=self._key(uri))
            return True
        except ClientError:
            return False

    # -- internals ---------------------------------------------------------
    def _cache_folder(self, key: str) -> Path:
        """One folder per object key, so a rewrite can clear just that object."""
        return self.cache_dir / hashlib.sha256(key.encode()).hexdigest()[:16]

    def _cache_path(self, key: str, etag: str) -> Path:
        name = hashlib.sha256(etag.encode()).hexdigest()[:16]
        return self._cache_folder(key) / f"{name}{Path(key).suffix}"

    def _invalidate(self, key: str) -> None:
        """Drop any cached copies of a key that has just been rewritten."""
        folder = self._cache_folder(key)
        if not folder.exists():
            return
        for path in folder.glob("*"):
            path.unlink(missing_ok=True)

    @staticmethod
    def _call(operation, *args, **kwargs):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return operation(*args, **kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"object storage call failed: {exc}") from exc
