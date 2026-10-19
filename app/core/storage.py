"""S3-совместимое хранилище (MinIO) или локальная папка для разработки и тестов."""

import io
import os
from pathlib import Path

from fastapi.concurrency import run_in_threadpool
from PIL import Image

from app.config import get_settings

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _s3():
    import boto3

    s = get_settings()
    return boto3.client(
        "s3", endpoint_url=s.s3_endpoint, aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key
    )


def _put_sync(key: str, data: bytes, content_type: str) -> None:
    s = get_settings()
    if s.storage_backend == "s3":
        _s3().put_object(Bucket=s.s3_bucket, Key=key, Body=data, ContentType=content_type,
                         CacheControl="public, max-age=31536000, immutable")
    else:
        path = Path(s.storage_local_dir) / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


async def put(key: str, data: bytes, content_type: str) -> None:
    await run_in_threadpool(_put_sync, key, data, content_type)


def public_url(key: str | None) -> str | None:
    if not key:
        return None
    return f"{get_settings().files_public_base_url.rstrip('/')}/{key}"


def make_webp_versions(data: bytes) -> tuple[bytes, bytes]:
    """Полный размер (до 1600px) и миниатюра (400px) в WebP."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        full = img.copy()
        full.thumbnail((1600, 1600))
        thumb = img.copy()
        thumb.thumbnail((400, 400))
        b1, b2 = io.BytesIO(), io.BytesIO()
        full.save(b1, "WEBP", quality=82)
        thumb.save(b2, "WEBP", quality=78)
        return b1.getvalue(), b2.getvalue()


def ensure_local_dir() -> None:
    s = get_settings()
    if s.storage_backend != "s3":
        os.makedirs(s.storage_local_dir, exist_ok=True)
