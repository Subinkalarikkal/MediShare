"""Private file storage.

Production: private S3-compatible bucket with short-lived signed URLs.
Development: a local private directory (outside any web-served path). The
local backend exists only so the app works without cloud credentials and
must not be used as permanent storage on Streamlit Community Cloud.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from config import get_settings
from database.models import UploadedFile
from utils.image_utils import validate_upload


class StorageBackend:
    name = "base"

    def put(self, key: str, data: bytes, content_type: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def get(self, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def signed_url(self, key: str, expires: int = 300) -> str | None:
        return None


class LocalBackend(StorageBackend):
    name = "local"

    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("Invalid storage key")
        return p

    def put(self, key, data, content_type):
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key):
        return self._path(key).read_bytes()


class S3Backend(StorageBackend):
    name = "s3"

    def __init__(self, settings):
        import boto3

        self.bucket = settings.storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.storage_endpoint or None,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
        )

    def put(self, key, data, content_type):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               ServerSideEncryption="AES256")

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def signed_url(self, key, expires=300):
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires)


_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        s = get_settings()
        _backend = S3Backend(s) if s.s3_enabled else LocalBackend(s.local_storage_dir)
    return _backend


def set_backend(backend: StorageBackend) -> None:
    global _backend
    _backend = backend


def store_upload(session: Session, *, owner_id: uuid.UUID, data: bytes, filename: str,
                 purpose: str, images_only: bool, organization_id: uuid.UUID | None = None) -> UploadedFile:
    """Validate, store privately, and record metadata. Returns the UploadedFile row."""
    vf = validate_upload(data, filename, max_mb=get_settings().max_upload_mb, images_only=images_only)
    backend = get_backend()
    key = f"{purpose}/{uuid.uuid4().hex}.{vf.extension}"
    backend.put(key, vf.data, vf.content_type)
    rec = UploadedFile(
        owner_user_id=owner_id, organization_id=organization_id, storage_backend=backend.name,
        storage_key=key, original_filename=vf.filename, content_type=vf.content_type,
        size_bytes=len(vf.data), sha256=hashlib.sha256(vf.data).hexdigest(), purpose=purpose,
    )
    session.add(rec)
    session.flush()
    return rec


def read_file(rec: UploadedFile) -> bytes:
    return get_backend().get(rec.storage_key)


def signed_url(rec: UploadedFile, expires: int = 300) -> str | None:
    return get_backend().signed_url(rec.storage_key, expires)
