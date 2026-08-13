"""Cloudflare R2 (S3-сумісне) об'єктне сховище для фото товарів.

Єдина точка доступу до R2 для всього проєкту: ingest-скрипт заливає сюди
WebP-майстри, бекенд (за потреби) читає як fallback, каталог роздає через
публічний CDN-URL.

Конфіг — у `.env` (R2_*). Без кредів модуль лишається «вимкненим»
(`is_enabled() == False`), решта програми працює як є — фото беруться
локально / з Drive.

Чому S3-протокол (boto3), а не власний API Cloudflare: нуль вендор-локіну —
той самий код працює проти R2 / AWS S3 / Backblaze / MinIO.
"""

from __future__ import annotations

import os
import logging
import threading
from functools import lru_cache
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "").strip()
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET = os.environ.get("R2_BUCKET", "bms-photos").strip()
# Публічний базовий URL роздачі (домен/CDN перед бакетом). Поки порожній —
# заповнюємо на Фазі 4. Має закінчуватись без слешу.
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")

# Тип контенту за розширенням (R2 не вгадує сам — важливо для коректної
# роздачі/кешу в браузері та каталозі).
_CONTENT_TYPES = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}
# Фото не змінюється під своїм іменем → кешуємо назавжди (immutable).
# Це різко знижує Class B операції (повторні покази йдуть з CDN-кешу).
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def is_enabled() -> bool:
    """Чи сконфігуровано R2 (є endpoint + ключі)."""
    return bool(R2_ENDPOINT and R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY)


def content_type_for(key_or_path: str) -> str:
    ext = os.path.splitext(key_or_path)[1].lower()
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


# Один клієнт на процес (boto3 client потокобезпечний для викликів).
_client_lock = threading.Lock()


@lru_cache(maxsize=1)
def _client():
    if not is_enabled():
        raise RuntimeError(
            "R2 не сконфігуровано: відсутні R2_ENDPOINT / R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY у .env"
        )
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",  # R2 ігнорує регіон, але boto3 вимагає значення
        config=Config(
            retries={"max_attempts": 5, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def get_client():
    """Повертає налаштований boto3 S3-клієнт для R2 (lazy, кешований)."""
    with _client_lock:
        return _client()


# ── Операції ────────────────────────────────────────────────────────────────
def object_exists(key: str) -> bool:
    """Чи є об'єкт із таким ключем (1 HEAD = 1 Class B)."""
    from botocore.exceptions import ClientError

    try:
        get_client().head_object(Bucket=R2_BUCKET, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def head(key: str) -> Optional[dict]:
    """Метадані об'єкта (size/etag/...), або None якщо нема."""
    from botocore.exceptions import ClientError

    try:
        return get_client().head_object(Bucket=R2_BUCKET, Key=key)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def upload_file(
    local_path: str,
    key: str,
    *,
    content_type: Optional[str] = None,
    cache_control: str = _IMMUTABLE_CACHE,
) -> None:
    """Заливає локальний файл у R2 під ключем `key` (Class A).

    Ключ = відносний шлях у бакеті (напр. `Сумки/Ф3916_01.webp`).
    """
    extra = {
        "ContentType": content_type or content_type_for(local_path),
        "CacheControl": cache_control,
    }
    get_client().upload_file(local_path, R2_BUCKET, key, ExtraArgs=extra)


def upload_bytes(
    data: bytes,
    key: str,
    *,
    content_type: Optional[str] = None,
    cache_control: str = _IMMUTABLE_CACHE,
) -> None:
    """Заливає байти у R2 під ключем `key`."""
    get_client().put_object(
        Bucket=R2_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type or content_type_for(key),
        CacheControl=cache_control,
    )


def download_bytes(key: str) -> bytes:
    """Читає об'єкт як байти (Class B). Кидає ClientError якщо нема."""
    return get_client().get_object(Bucket=R2_BUCKET, Key=key)["Body"].read()


def delete(key: str) -> None:
    get_client().delete_object(Bucket=R2_BUCKET, Key=key)


def list_keys(prefix: str = "") -> Iterator[str]:
    """Ітерує всі ключі з префіксом (пагінація; кожна сторінка = 1 Class A)."""
    paginator = get_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"]


def public_url(key: str) -> Optional[str]:
    """Публічний URL для роздачі (каталог). None, якщо домен ще не підключено.

    Опційний `?width=` ріже Worker/Image Resizing — додається на боці фронту.
    """
    if not R2_PUBLIC_BASE_URL:
        return None
    from urllib.parse import quote

    return f"{R2_PUBLIC_BASE_URL}/{quote(key)}"
