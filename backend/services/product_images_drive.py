"""Google Drive provider для фото товарів.

Сканує корінь "Товар" та всі підпапки (Взуття, Сумки тощо) — будує
in-memory index `{normalized_pnum: [(filename, file_id)]}` для O(1) lookup.

Доступ — через service account credentials з mcp-google-sheets/working_credentials.json.
Папку потрібно один раз розшарити на email сервісного акаунта.

Кеш індексу: 15 хв TTL (config via env).
Кеш байтів файлу: на диск (~/.cache/bms_drive_images) — щоб не битись об quota Drive API.
"""

from __future__ import annotations

import io
import os
import re
import time
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DRIVE_ROOT_FOLDER_ID = os.environ.get(
    "PRODUCT_IMAGES_DRIVE_FOLDER_ID",
    "19fcEnUDL9G4cZagUyPs61hEIcbcT2n-d",  # "Товар"
)
DRIVE_CREDS_PATH = os.environ.get(
    "GOOGLE_DRIVE_CREDS_PATH",
    "/Users/i.malashenko/Desktop/react-fastapi-app/mcp-google-sheets/working_credentials.json",
)
DRIVE_INDEX_TTL_SEC = int(os.environ.get("PRODUCT_IMAGES_DRIVE_TTL", "900"))  # 15 хв
DRIVE_BYTES_CACHE_DIR = os.path.expanduser(
    os.environ.get("PRODUCT_IMAGES_DRIVE_CACHE_DIR", "~/.cache/bms_drive_images")
)

URL_PREFIX_DRIVE = "/product-images-drive"  # proxy endpoint

# Re-use matching/sort logic from local provider via direct import.

_index_lock = threading.Lock()
_index_state: dict = {
    "by_pnum": {},          # {pnum_normalized_lower: [(filename, file_id), ...]}
    "expires_at": 0.0,
    "last_rebuild_at": 0.0,
    "total_files": 0,
}


@dataclass
class DriveImageEntry:
    filename: str
    file_id: str


# ── Drive service (lazy, thread-safe) ─────────────────────────────────────────
_service = None
_service_lock = threading.Lock()


def _get_service():
    global _service
    if _service is not None:
        return _service
    with _service_lock:
        if _service is not None:
            return _service
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            logger.warning("google-api-python-client not installed — Drive provider disabled")
            return None
        if not os.path.isfile(DRIVE_CREDS_PATH):
            logger.warning(f"Drive creds not found: {DRIVE_CREDS_PATH}")
            return None
        creds = Credentials.from_service_account_file(
            DRIVE_CREDS_PATH,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        _service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return _service


# ── Helpers ───────────────────────────────────────────────────────────────────
def _extract_pnum_from_filename(filename: str) -> Optional[str]:
    """Витягує первинний номер товару з імені файла.

    `Ф3042_04.jpeg` → `Ф3042`
    `#Л145_main.jpg` → `Л145`
    `A1247_07.jpeg` → `A1247`
    Returns lowercase для регістронезалежного lookup.
    """
    base = os.path.splitext(filename)[0]
    m = re.match(r"^#?([^\s_.\-#]+)", base)
    if not m:
        return None
    return m.group(1).strip().lower()


# ── Index build/cache ─────────────────────────────────────────────────────────
def _rebuild_index() -> Dict[str, List[Tuple[str, str]]]:
    """Скан Drive: всі підпапки в корені 'Товар' + усі фото у них.
    Returns {pnum_lower: [(filename, file_id)]}.
    """
    service = _get_service()
    if not service:
        return {}

    by_pnum: Dict[str, List[Tuple[str, str]]] = {}
    total = 0

    try:
        # 1. Усі підпапки кореня (Взуття, Сумки, ...)
        resp = service.files().list(
            q=(f"'{DRIVE_ROOT_FOLDER_ID}' in parents "
               f"and mimeType='application/vnd.google-apps.folder' "
               f"and trashed=false"),
            fields="files(id,name)",
            pageSize=100,
        ).execute()
        subfolders = resp.get("files", [])
        logger.info(f"[drive-images] Found {len(subfolders)} subfolders in root")

        # 2. Усі image-файли в кожній (з paging)
        for sf in subfolders:
            page_token = None
            while True:
                resp = service.files().list(
                    q=(f"'{sf['id']}' in parents "
                       f"and mimeType contains 'image' "
                       f"and trashed=false"),
                    fields="nextPageToken, files(id,name)",
                    pageSize=1000,
                    pageToken=page_token,
                ).execute()
                for f in resp.get("files", []):
                    pnum = _extract_pnum_from_filename(f["name"])
                    if pnum:
                        by_pnum.setdefault(pnum, []).append((f["name"], f["id"]))
                        total += 1
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            logger.debug(f"[drive-images] {sf['name']}: cumulative total {total} files indexed")

    except Exception as e:
        logger.error(f"[drive-images] Index rebuild failed: {e}")
        return {}

    logger.info(f"[drive-images] Index rebuilt: {total} files in {len(by_pnum)} pnum buckets")
    return by_pnum


def _get_index() -> Dict[str, List[Tuple[str, str]]]:
    now = time.time()
    if now < _index_state["expires_at"]:
        return _index_state["by_pnum"]
    with _index_lock:
        if now < _index_state["expires_at"]:  # double-check
            return _index_state["by_pnum"]
        idx = _rebuild_index()
        _index_state["by_pnum"] = idx
        _index_state["expires_at"] = now + DRIVE_INDEX_TTL_SEC
        _index_state["last_rebuild_at"] = now
        _index_state["total_files"] = sum(len(v) for v in idx.values())
        return idx


def invalidate_drive_index() -> None:
    """Manual invalidation (call after upload to Drive, or via admin trigger)."""
    with _index_lock:
        _index_state["expires_at"] = 0.0
    logger.info("[drive-images] Index manually invalidated")


def get_drive_index_stats() -> dict:
    return {
        "total_files": _index_state["total_files"],
        "buckets": len(_index_state["by_pnum"]),
        "last_rebuild_at": _index_state["last_rebuild_at"],
        "expires_at": _index_state["expires_at"],
        "valid": time.time() < _index_state["expires_at"],
    }


# ── Public API ────────────────────────────────────────────────────────────────
def list_drive_images_for(target_normalized: str) -> List[DriveImageEntry]:
    """Знайти фото товару в Drive за normalized productnumber (без #, lowercase).

    Caller повинен передати target вже з `_normalize_number()` з product_images.py.
    """
    if not target_normalized:
        return []
    idx = _get_index()
    matches = idx.get(target_normalized.lower(), [])
    return [DriveImageEntry(filename=fn, file_id=fid) for fn, fid in matches]


# ── File bytes (proxy endpoint) ───────────────────────────────────────────────
def _cache_path(file_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", file_id)
    return os.path.join(DRIVE_BYTES_CACHE_DIR, safe)


def get_drive_file_bytes(file_id: str) -> Optional[Tuple[bytes, str]]:
    """Завантажити байти файлу з Drive (через disk-cache).

    Returns (bytes, mime_type) або None якщо файл недоступний.
    """
    cache_file = _cache_path(file_id)
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                data = f.read()
            mime_file = cache_file + ".mime"
            mime = "application/octet-stream"
            if os.path.exists(mime_file):
                with open(mime_file, "r") as f:
                    mime = f.read().strip() or mime
            return data, mime
        except OSError:
            pass  # fall through to re-fetch

    service = _get_service()
    if not service:
        return None

    try:
        meta = service.files().get(fileId=file_id, fields="mimeType,name,size").execute()
        from googleapiclient.http import MediaIoBaseDownload
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        data = fh.getvalue()
        mime = meta.get("mimeType", "application/octet-stream")

        # Save to cache
        try:
            os.makedirs(DRIVE_BYTES_CACHE_DIR, exist_ok=True)
            with open(cache_file, "wb") as f:
                f.write(data)
            with open(cache_file + ".mime", "w") as f:
                f.write(mime)
        except OSError as e:
            logger.warning(f"[drive-images] Cache write failed: {e}")

        return data, mime
    except Exception as e:
        logger.warning(f"[drive-images] Failed to fetch file {file_id}: {e}")
        return None
