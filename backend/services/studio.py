"""Майстерня публікацій: галерея, фірмові шрифти й власні (нетоварні) пости.

Розподіл обов'язків між браузером і бекендом тут навмисний:

* **Малює браузер.** Редактор — це SVG-документ, і фінальний растр народжується
  з того самого документа, який людина бачить на екрані. Другого рендера (на
  PIL) немає свідомо: два незалежні рендери одного макета з градієнтами,
  тінями й обведеннями розходяться в дрібницях, і кожна дрібниця — це «на
  прев'ю було не так».
* **Зберігає бекенд.** Сюди приходить уже готовий PNG/JPEG, лягає в R2 поруч
  із макетом (`spec_json`) — і саме файл потім забирає мережа за URL. Тому
  запланована публікація не потребує ані відкритого редактора, ані програми.

Файли віддаються ЧЕРЕЗ ЦЕЙ БЕКЕНД, а не прямим CDN-посиланням. Причина
технічна: щоб вшити фото й шрифт у SVG перед рендером, браузер має прочитати
їхні байти, а крос-доменний файл або «отруює» canvas, або вимагає CORS на
бакеті. Свій ендпоінт = те саме походження = жодних несподіванок.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

try:
    from services import r2_storage
except ImportError:  # запуск з кореня репо
    from backend.services import r2_storage  # type: ignore


# ── Константи ───────────────────────────────────────────────────────────────

R2_MEDIA_PREFIX = "studio/media"
R2_THUMB_PREFIX = "studio/thumbs"
R2_FONT_PREFIX = "studio/fonts"
R2_RENDER_PREFIX = "studio/posts"

CACHE_DIR = os.path.expanduser(
    os.environ.get("STUDIO_CACHE_DIR", "~/.cache/bms_studio")
)

# Довша сторона майстра галереї. 2560 px вистачає навіть на фон 1080×1920 із
# запасом на кроп і масштабування, але не тягне в хмару 12-мегапіксельні
# оригінали з телефона.
MASTER_MAX_SIDE = 2560
THUMB_MAX_SIDE = 480
_WEBP_QUALITY = 88
_THUMB_QUALITY = 78

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_FONT_BYTES = 12 * 1024 * 1024

FONT_FORMATS = {".ttf": "ttf", ".otf": "otf", ".woff": "woff", ".woff2": "woff2"}

# Формати полотна. Один словник на весь проєкт: і редактор, і майбутня
# публікація мають однаково розуміти, що таке «story».
CANVAS_FORMATS: Dict[str, Dict[str, Any]] = {
    "story":    {"label": "Сторіс 9:16",     "width": 1080, "height": 1920},
    "square":   {"label": "Квадрат 1:1",     "width": 1080, "height": 1080},
    "portrait": {"label": "Портрет 4:5",     "width": 1080, "height": 1350},
    "landscape": {"label": "Горизонт 1.91:1", "width": 1200, "height": 628},
}
DEFAULT_FORMAT = "story"

# Які формати приймає кожна мережа. Список свідомо описовий: на 1-му етапі він
# керує лише вибором у редакторі, публікаційний контур приходить пізніше.
PLATFORM_FORMATS: Dict[str, Dict[str, Any]] = {
    "telegram":  {"label": "Telegram",  "formats": ["square", "portrait", "landscape", "story"]},
    "instagram": {"label": "Instagram", "formats": ["story", "square", "portrait"]},
    "facebook":  {"label": "Facebook",  "formats": ["story", "square", "portrait", "landscape"]},
    "viber":     {"label": "Viber",     "formats": ["square", "portrait", "landscape"]},
}

POST_STATUSES = ("draft", "ready", "scheduled", "published", "archived")

_dir_lock = threading.Lock()


class StudioError(RuntimeError):
    """Помилка, яку роут перетворює на 4xx з людським поясненням."""


def _r2():
    if not r2_storage.is_enabled():
        raise StudioError(
            "Cloudflare R2 не налаштований (R2_ENDPOINT / ключі у .env) — "
            "галерея майстерні зберігає файли лише в хмарі"
        )
    return r2_storage


def canvas_formats() -> List[dict]:
    return [{"key": key, **value} for key, value in CANVAS_FORMATS.items()]


def platforms() -> List[dict]:
    return [{"key": key, **value} for key, value in PLATFORM_FORMATS.items()]


# ── Локальний кеш роздачі ───────────────────────────────────────────────────

def _cache_path(key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, digest[:2], digest)


def _read_cached(key: str) -> Optional[bytes]:
    path = _cache_path(key)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _write_cached(key: str, data: bytes) -> None:
    path = _cache_path(key)
    try:
        with _dir_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except OSError as exc:  # кеш — прискорення, а не спосіб втратити файл
        logger.debug("studio: кеш не записано (%s): %s", key, exc)


def object_bytes(key: str) -> bytes:
    """Байти об'єкта з локального кешу, інакше — з R2 (і в кеш)."""
    cached = _read_cached(key)
    if cached is not None:
        return cached
    data = _r2().download_bytes(key)
    _write_cached(key, data)
    return data


# ── Обробка зображень ───────────────────────────────────────────────────────

def _prepare_image(raw: bytes) -> Tuple[bytes, bytes, int, int, bool]:
    """Оригінал → (майстер WebP, мініатюра WebP, ширина, висота, чи є альфа).

    Прозорість зберігаємо: у майстерню кладуть не лише фони, а й накладки —
    логотипи й наклейки, де альфа є сенсом файлу.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # noqa: BLE001 — будь-який збій декодера
        raise StudioError(f"Не вдалося прочитати зображення: {exc}") from exc

    # Орієнтація з EXIF: інакше фото з телефона лягає в макет боком.
    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if has_alpha else "RGB")

    master = image
    if max(image.size) > MASTER_MAX_SIDE:
        master = ImageOps.contain(
            image, (MASTER_MAX_SIDE, MASTER_MAX_SIDE), Image.Resampling.LANCZOS
        )

    master_buf = io.BytesIO()
    master.save(master_buf, "WEBP", quality=_WEBP_QUALITY, method=5)

    thumb = ImageOps.contain(
        master, (THUMB_MAX_SIDE, THUMB_MAX_SIDE), Image.Resampling.LANCZOS
    )
    thumb_buf = io.BytesIO()
    thumb.save(thumb_buf, "WEBP", quality=_THUMB_QUALITY, method=4)

    return (master_buf.getvalue(), thumb_buf.getvalue(),
            master.size[0], master.size[1], has_alpha)


# ── Підбірки ────────────────────────────────────────────────────────────────

_COLLECTION_SELECT = """
    SELECT id, kind, name, sort_order, created_at, updated_at
    FROM studio_collections
"""


def list_collections(db: Session, kind: Optional[str] = None) -> List[dict]:
    where = " WHERE kind = :kind" if kind else ""
    rows = db.execute(
        text(_COLLECTION_SELECT + where + " ORDER BY sort_order, name"),
        {"kind": kind} if kind else {},
    ).mappings().all()
    return [dict(row) for row in rows]


def create_collection(db: Session, *, kind: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise StudioError("Порожня назва підбірки")
    if kind not in ("media", "post"):
        raise StudioError(f"Невідомий різновид підбірки: {kind}")
    row = db.execute(text("""
        INSERT INTO studio_collections(kind, name)
        VALUES (:kind, :name)
        ON CONFLICT (kind, name) DO UPDATE SET updated_at = now()
        RETURNING id, kind, name, sort_order, created_at, updated_at
    """), {"kind": kind, "name": name}).mappings().one()
    db.commit()
    return dict(row)


def rename_collection(db: Session, collection_id: int, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise StudioError("Порожня назва підбірки")
    row = db.execute(text("""
        UPDATE studio_collections SET name = :name, updated_at = now()
        WHERE id = :id
        RETURNING id, kind, name, sort_order, created_at, updated_at
    """), {"id": collection_id, "name": name}).mappings().first()
    if row is None:
        raise StudioError("Підбірку не знайдено")
    db.commit()
    return dict(row)


def delete_collection(db: Session, collection_id: int) -> dict:
    """Прибрати підбірку. Вміст НЕ видаляється — лише втрачає приналежність
    (`ON DELETE SET NULL`): підбірка тут — ярлик, а не тека з файлами."""
    deleted = db.execute(
        text("DELETE FROM studio_collections WHERE id = :id RETURNING id"),
        {"id": collection_id},
    ).mappings().first()
    if deleted is None:
        raise StudioError("Підбірку не знайдено")
    db.commit()
    return {"deleted": collection_id}


# ── Галерея ─────────────────────────────────────────────────────────────────

_ASSET_SELECT = """
    SELECT id, sha256, r2_key, url, thumb_key, thumb_url, filename, title,
           mime, width, height, bytes, has_alpha, collection_id, tags,
           sort_order, created_at, updated_at
    FROM studio_assets
"""


def _asset_row(row) -> dict:
    item = dict(row)
    item["tags"] = item.get("tags") or []
    # Роздача йде через власний бекенд — фронт не має знати про R2 нічого.
    item["src"] = f"/api/studio/assets/{item['id']}/file"
    item["thumb_src"] = f"/api/studio/assets/{item['id']}/file?thumb=1"
    return item


def list_assets(db: Session, *, collection_id: Optional[int] = None,
                search: Optional[str] = None, limit: int = 200,
                offset: int = 0) -> dict:
    clauses: List[str] = []
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 500)),
                              "offset": max(0, int(offset))}
    if collection_id:
        clauses.append("collection_id = :collection_id")
        params["collection_id"] = collection_id
    if search:
        clauses.append("(title ILIKE :search OR filename ILIKE :search)")
        params["search"] = f"%{search.strip()}%"
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.execute(
        text(_ASSET_SELECT + where +
             " ORDER BY sort_order, id DESC LIMIT :limit OFFSET :offset"),
        params,
    ).mappings().all()
    total = int(db.execute(
        text("SELECT COUNT(*) FROM studio_assets" + where), params,
    ).scalar() or 0)
    return {"items": [_asset_row(row) for row in rows], "total": total}


def get_asset(db: Session, asset_id: int) -> dict:
    row = db.execute(text(_ASSET_SELECT + " WHERE id = :id"),
                     {"id": asset_id}).mappings().first()
    if row is None:
        raise StudioError("Фото не знайдено")
    return _asset_row(row)


def add_asset(db: Session, *, filename: str, raw: bytes,
              collection_id: Optional[int] = None,
              title: Optional[str] = None) -> dict:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise StudioError(
            f"Файл завеликий ({len(raw) // (1024 * 1024)} МБ), стеля — "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} МБ"
        )
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.execute(text(_ASSET_SELECT + " WHERE sha256 = :sha"),
                          {"sha": digest}).mappings().first()
    if existing is not None:
        # Той самий файл уже в галереї — не плодимо копію в хмарі, повертаємо
        # наявний запис. Людина побачить, що фото вже є, замість дубля.
        return {**_asset_row(existing), "duplicate": True}

    master, thumb, width, height, has_alpha = _prepare_image(raw)
    r2 = _r2()
    key = f"{R2_MEDIA_PREFIX}/{digest[:2]}/{digest}.webp"
    thumb_key = f"{R2_THUMB_PREFIX}/{digest[:2]}/{digest}.webp"
    r2.upload_bytes(master, key, content_type="image/webp")
    r2.upload_bytes(thumb, thumb_key, content_type="image/webp")
    _write_cached(key, master)
    _write_cached(thumb_key, thumb)

    row = db.execute(text("""
        INSERT INTO studio_assets(sha256, r2_key, url, thumb_key, thumb_url,
                                  filename, title, mime, width, height, bytes,
                                  has_alpha, collection_id)
        VALUES (:sha, :key, :url, :thumb_key, :thumb_url, :filename, :title,
                'image/webp', :width, :height, :bytes, :has_alpha, :collection_id)
        RETURNING id, sha256, r2_key, url, thumb_key, thumb_url, filename, title,
                  mime, width, height, bytes, has_alpha, collection_id, tags,
                  sort_order, created_at, updated_at
    """), {
        "sha": digest, "key": key, "url": r2.public_url(key),
        "thumb_key": thumb_key, "thumb_url": r2.public_url(thumb_key),
        "filename": filename or f"{digest[:12]}.webp",
        "title": (title or "").strip() or None,
        "width": width, "height": height, "bytes": len(master),
        "has_alpha": has_alpha, "collection_id": collection_id,
    }).mappings().one()
    db.commit()
    return {**_asset_row(row), "duplicate": False}


def update_asset(db: Session, asset_id: int, *, title: Optional[str] = None,
                 collection_id: Optional[int] = None,
                 clear_collection: bool = False,
                 tags: Optional[Sequence[str]] = None,
                 sort_order: Optional[int] = None) -> dict:
    sets: List[str] = []
    params: Dict[str, Any] = {"id": asset_id}
    if title is not None:
        sets.append("title = :title")
        params["title"] = title.strip() or None
    if clear_collection:
        sets.append("collection_id = NULL")
    elif collection_id is not None:
        sets.append("collection_id = :collection_id")
        params["collection_id"] = collection_id
    if tags is not None:
        sets.append("tags = CAST(:tags AS jsonb)")
        params["tags"] = json.dumps([str(tag) for tag in tags])
    if sort_order is not None:
        sets.append("sort_order = :sort_order")
        params["sort_order"] = int(sort_order)
    if not sets:
        return get_asset(db, asset_id)
    sets.append("updated_at = now()")
    row = db.execute(
        text(f"UPDATE studio_assets SET {', '.join(sets)} WHERE id = :id"
             " RETURNING id, sha256, r2_key, url, thumb_key, thumb_url,"
             " filename, title, mime, width, height, bytes, has_alpha,"
             " collection_id, tags, sort_order, created_at, updated_at"),
        params,
    ).mappings().first()
    if row is None:
        raise StudioError("Фото не знайдено")
    db.commit()
    return _asset_row(row)


def reorder_assets(db: Session, ordered_ids: Sequence[int]) -> dict:
    """Порядок задає людина перетягуванням — записуємо індекси, як у картці
    товару (перенумерація, а не перейменування)."""
    for index, asset_id in enumerate(ordered_ids):
        db.execute(
            text("UPDATE studio_assets SET sort_order = :order, updated_at = now()"
                 " WHERE id = :id"),
            {"order": index, "id": int(asset_id)},
        )
    db.commit()
    return {"reordered": len(ordered_ids)}


def delete_asset(db: Session, asset_id: int) -> dict:
    row = db.execute(
        text("DELETE FROM studio_assets WHERE id = :id"
             " RETURNING id, r2_key, thumb_key"),
        {"id": asset_id},
    ).mappings().first()
    if row is None:
        raise StudioError("Фото не знайдено")
    db.commit()
    # Об'єкт у хмарі прибираємо ПІСЛЯ коміту й ніколи не валимо через це
    # відповідь: осиротілий об'єкт у R2 коштує копійки, а помилка тут виглядала
    # б як «фото не видалилось», хоча з галереї воно вже зникло.
    for key in (row["r2_key"], row["thumb_key"]):
        if not key:
            continue
        try:
            r2_storage.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("studio: не вдалось прибрати %s з R2: %s", key, exc)
    return {"deleted": asset_id}


def asset_bytes(db: Session, asset_id: int, *, thumb: bool = False) -> Tuple[bytes, str]:
    row = db.execute(
        text("SELECT r2_key, thumb_key, mime FROM studio_assets WHERE id = :id"),
        {"id": asset_id},
    ).mappings().first()
    if row is None:
        raise StudioError("Фото не знайдено")
    key = (row["thumb_key"] if thumb else None) or row["r2_key"]
    return object_bytes(key), (row["mime"] or "image/webp")


# ── Шрифти ──────────────────────────────────────────────────────────────────

_FONT_SELECT = """
    SELECT id, family, weight, style, label, sha256, r2_key, url, format,
           filename, bytes, has_cyrillic, is_default, created_at, updated_at
    FROM studio_fonts
"""

# Натяки на накреслення в імені файлу — коли метадані шрифта нечитабельні
# (woff2 Pillow не відкриває) або їх немає.
_WEIGHT_HINTS: Sequence[Tuple[str, int]] = (
    ("thin", 100), ("extralight", 200), ("ultralight", 200), ("light", 300),
    ("regular", 400), ("normal", 400), ("book", 400), ("medium", 500),
    ("semibold", 600), ("demibold", 600), ("extrabold", 800), ("ultrabold", 800),
    ("black", 900), ("heavy", 900), ("bold", 700),
)


def _font_meta(raw: bytes, filename: str, ext: str) -> Tuple[str, int, str, bool]:
    """(родина, вага, стиль, чи є кирилиця).

    Спершу питаємо сам файл через Pillow — це чесна назва родини, яку побачить
    і рендер. Якщо формат нечитабельний (woff/woff2), розбираємо ім'я файлу.
    """
    family: Optional[str] = None
    style_name = ""
    has_cyrillic = False
    if ext in ("ttf", "otf"):
        try:
            from PIL import ImageFont

            font = ImageFont.truetype(io.BytesIO(raw), size=32)
            name, style_name = font.getname()
            family = (name or "").strip() or None
            # Порожній прямокутник замість «Ї» — найдорожча помилка в макеті
            # українською, тому перевіряємо кирилицю одразу при заливці.
            mask = font.getmask("Ї", mode="1")
            has_cyrillic = bool(mask.getbbox())
        except Exception as exc:  # noqa: BLE001
            logger.debug("studio: метадані шрифта %s не прочитано: %s", filename, exc)

    stem = re.sub(r"\.[A-Za-z0-9]+$", "", os.path.basename(filename or ""))
    lowered = stem.lower()
    if not family:
        family = re.split(r"[-_]", stem)[0].strip() or stem or "Фірмовий"

    weight = 400
    for token, value in _WEIGHT_HINTS:
        if token in (style_name or "").lower() or token in lowered:
            weight = value
            break
    style = "italic" if ("italic" in (style_name or "").lower()
                         or "italic" in lowered or "oblique" in lowered) else "normal"
    return family, weight, style, has_cyrillic


def list_fonts(db: Session) -> List[dict]:
    rows = db.execute(
        text(_FONT_SELECT + " ORDER BY family, weight, style")
    ).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        item["src"] = f"/api/studio/fonts/{item['id']}/file"
        items.append(item)
    return items


def add_font(db: Session, *, filename: str, raw: bytes,
             family: Optional[str] = None, weight: Optional[int] = None,
             style: Optional[str] = None) -> dict:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in FONT_FORMATS:
        raise StudioError(
            f"Формат {ext or '—'} не підходить. Приймаються .ttf, .otf, .woff, .woff2"
        )
    if len(raw) > MAX_FONT_BYTES:
        raise StudioError("Шрифт завеликий (стеля — 12 МБ)")
    fmt = FONT_FORMATS[ext]
    digest = hashlib.sha256(raw).hexdigest()
    existing = db.execute(text(_FONT_SELECT + " WHERE sha256 = :sha"),
                          {"sha": digest}).mappings().first()
    if existing is not None:
        item = dict(existing)
        item["src"] = f"/api/studio/fonts/{item['id']}/file"
        return {**item, "duplicate": True}

    auto_family, auto_weight, auto_style, has_cyrillic = _font_meta(raw, filename, fmt)
    family = (family or "").strip() or auto_family
    weight = int(weight or auto_weight)
    style = (style or auto_style) if (style in ("normal", "italic") or not style) else auto_style
    weight = max(100, min(900, weight))

    r2 = _r2()
    key = f"{R2_FONT_PREFIX}/{digest[:2]}/{digest}.{fmt}"
    r2.upload_bytes(raw, key, content_type=r2.content_type_for(f".{fmt}"))
    _write_cached(key, raw)

    row = db.execute(text("""
        INSERT INTO studio_fonts(family, weight, style, label, sha256, r2_key,
                                 url, format, filename, bytes, has_cyrillic)
        VALUES (:family, :weight, :style, :label, :sha, :key, :url, :format,
                :filename, :bytes, :has_cyrillic)
        ON CONFLICT (family, weight, style) DO UPDATE SET
            sha256 = EXCLUDED.sha256, r2_key = EXCLUDED.r2_key,
            url = EXCLUDED.url, format = EXCLUDED.format,
            filename = EXCLUDED.filename, bytes = EXCLUDED.bytes,
            has_cyrillic = EXCLUDED.has_cyrillic, updated_at = now()
        RETURNING id, family, weight, style, label, sha256, r2_key, url, format,
                  filename, bytes, has_cyrillic, is_default, created_at, updated_at
    """), {
        "family": family, "weight": weight, "style": style,
        "label": os.path.splitext(os.path.basename(filename or ""))[0][:160] or None,
        "sha": digest, "key": key, "url": r2.public_url(key), "format": fmt,
        "filename": filename or f"{digest[:12]}.{fmt}", "bytes": len(raw),
        "has_cyrillic": has_cyrillic,
    }).mappings().one()
    db.commit()
    item = dict(row)
    item["src"] = f"/api/studio/fonts/{item['id']}/file"
    return {**item, "duplicate": False}


def delete_font(db: Session, font_id: int) -> dict:
    row = db.execute(
        text("DELETE FROM studio_fonts WHERE id = :id RETURNING id, r2_key"),
        {"id": font_id},
    ).mappings().first()
    if row is None:
        raise StudioError("Шрифт не знайдено")
    db.commit()
    try:
        r2_storage.delete(row["r2_key"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("studio: шрифт %s лишився в R2: %s", row["r2_key"], exc)
    return {"deleted": font_id}


def font_bytes(db: Session, font_id: int) -> Tuple[bytes, str]:
    row = db.execute(
        text("SELECT r2_key, format FROM studio_fonts WHERE id = :id"),
        {"id": font_id},
    ).mappings().first()
    if row is None:
        raise StudioError("Шрифт не знайдено")
    mime = {
        "ttf": "font/ttf", "otf": "font/otf",
        "woff": "font/woff", "woff2": "font/woff2",
    }[row["format"]]
    return object_bytes(row["r2_key"]), mime


# ── Пости ───────────────────────────────────────────────────────────────────

_POST_SELECT = """
    SELECT id, title, status, base_format, spec_json, targets_json, caption,
           preview_key, preview_url, renders_json, collection_id, scheduled_at,
           published_at, created_at, updated_at
    FROM studio_posts
"""


def _post_row(row, *, with_spec: bool = True) -> dict:
    item = dict(row)
    item["targets"] = item.pop("targets_json", None) or []
    item["renders"] = item.pop("renders_json", None) or {}
    spec = item.pop("spec_json", None) or {}
    if with_spec:
        item["spec"] = spec
    item["preview_src"] = (
        f"/api/studio/posts/{item['id']}/preview" if item.get("preview_key") else None
    )
    return item


def list_posts(db: Session, *, status: Optional[str] = None,
               collection_id: Optional[int] = None,
               search: Optional[str] = None, limit: int = 100,
               offset: int = 0) -> dict:
    clauses: List[str] = []
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 200)),
                              "offset": max(0, int(offset))}
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if collection_id:
        clauses.append("collection_id = :collection_id")
        params["collection_id"] = collection_id
    if search:
        clauses.append("(title ILIKE :search OR caption ILIKE :search)")
        params["search"] = f"%{search.strip()}%"
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.execute(
        text(_POST_SELECT + where +
             " ORDER BY updated_at DESC LIMIT :limit OFFSET :offset"),
        params,
    ).mappings().all()
    total = int(db.execute(
        text("SELECT COUNT(*) FROM studio_posts" + where), params,
    ).scalar() or 0)
    # У списку макет не потрібен — він важкий, а показуємо ми прев'ю.
    return {"items": [_post_row(row, with_spec=False) for row in rows],
            "total": total}


def get_post(db: Session, post_id: int) -> dict:
    row = db.execute(text(_POST_SELECT + " WHERE id = :id"),
                     {"id": post_id}).mappings().first()
    if row is None:
        raise StudioError("Пост не знайдено")
    return _post_row(row)


def _validate_targets(raw: Any) -> List[dict]:
    if not isinstance(raw, list):
        return []
    targets: List[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        platform = str(entry.get("platform") or "").strip()
        if platform not in PLATFORM_FORMATS:
            continue
        fmt = str(entry.get("format") or DEFAULT_FORMAT)
        if fmt not in PLATFORM_FORMATS[platform]["formats"]:
            fmt = PLATFORM_FORMATS[platform]["formats"][0]
        settings = entry.get("settings")
        targets.append({
            "platform": platform,
            "format": fmt,
            "enabled": bool(entry.get("enabled", True)),
            "settings": settings if isinstance(settings, dict) else {},
        })
    return targets


def create_post(db: Session, payload: dict) -> dict:
    base_format = str(payload.get("base_format") or DEFAULT_FORMAT)
    if base_format not in CANVAS_FORMATS:
        raise StudioError(f"Невідомий формат полотна: {base_format}")
    row = db.execute(text("""
        INSERT INTO studio_posts(title, base_format, spec_json, targets_json,
                                 caption, collection_id)
        VALUES (:title, :base_format, CAST(:spec AS jsonb),
                CAST(:targets AS jsonb), :caption, :collection_id)
        RETURNING id, title, status, base_format, spec_json, targets_json,
                  caption, preview_key, preview_url, renders_json,
                  collection_id, scheduled_at, published_at, created_at, updated_at
    """), {
        "title": (str(payload.get("title") or "").strip() or "Без назви")[:200],
        "base_format": base_format,
        "spec": json.dumps(payload.get("spec") or {}),
        "targets": json.dumps(_validate_targets(payload.get("targets"))),
        "caption": str(payload.get("caption") or ""),
        "collection_id": payload.get("collection_id"),
    }).mappings().one()
    db.commit()
    return _post_row(row)


def update_post(db: Session, post_id: int, payload: dict) -> dict:
    sets: List[str] = []
    params: Dict[str, Any] = {"id": post_id}
    if "title" in payload:
        sets.append("title = :title")
        params["title"] = (str(payload.get("title") or "").strip() or "Без назви")[:200]
    if "status" in payload:
        status = str(payload.get("status") or "draft")
        if status not in POST_STATUSES:
            raise StudioError(f"Невідомий статус: {status}")
        sets.append("status = :status")
        params["status"] = status
    if "base_format" in payload:
        base_format = str(payload.get("base_format") or DEFAULT_FORMAT)
        if base_format not in CANVAS_FORMATS:
            raise StudioError(f"Невідомий формат полотна: {base_format}")
        sets.append("base_format = :base_format")
        params["base_format"] = base_format
    if "spec" in payload:
        sets.append("spec_json = CAST(:spec AS jsonb)")
        params["spec"] = json.dumps(payload.get("spec") or {})
    if "targets" in payload:
        sets.append("targets_json = CAST(:targets AS jsonb)")
        params["targets"] = json.dumps(_validate_targets(payload.get("targets")))
    if "caption" in payload:
        sets.append("caption = :caption")
        params["caption"] = str(payload.get("caption") or "")
    if "collection_id" in payload:
        sets.append("collection_id = :collection_id")
        params["collection_id"] = payload.get("collection_id")
    if "scheduled_at" in payload:
        sets.append("scheduled_at = :scheduled_at")
        params["scheduled_at"] = payload.get("scheduled_at")
    if not sets:
        return get_post(db, post_id)
    sets.append("updated_at = now()")
    row = db.execute(
        text(f"UPDATE studio_posts SET {', '.join(sets)} WHERE id = :id"
             " RETURNING id, title, status, base_format, spec_json, targets_json,"
             " caption, preview_key, preview_url, renders_json, collection_id,"
             " scheduled_at, published_at, created_at, updated_at"),
        params,
    ).mappings().first()
    if row is None:
        raise StudioError("Пост не знайдено")
    db.commit()
    return _post_row(row)


def delete_post(db: Session, post_id: int) -> dict:
    row = db.execute(
        text("DELETE FROM studio_posts WHERE id = :id"
             " RETURNING id, preview_key, renders_json"),
        {"id": post_id},
    ).mappings().first()
    if row is None:
        raise StudioError("Пост не знайдено")
    db.commit()
    keys = {row["preview_key"]} | {
        entry.get("key") for entry in (row["renders_json"] or {}).values()
        if isinstance(entry, dict)
    }
    # Плюс усе, що лежить під власним префіксом поста. Записані ключі — це те,
    # що ми ПАМ'ЯТАЄМО, а перезбирання кадру лишає в хмарі й попередні версії
    # (ключ містить відбиток вмісту). Без прибирання за префіксом вони жили б
    # там вічно, і про них не знав би вже ніхто.
    try:
        keys |= set(r2_storage.list_keys(f"{R2_RENDER_PREFIX}/{post_id}/"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("studio: не вдалось перелічити растри поста %s: %s", post_id, exc)
    for key in keys:
        if not key:
            continue
        try:
            r2_storage.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("studio: растр %s лишився в R2: %s", key, exc)
    # Публікаційні JPEG (`studio/publish/...`) свідомо НЕ чіпаємо: на них може
    # досі посилатися вже опублікований допис у мережі, і прибирання зробило б
    # у чужій стрічці порожній кадр. Пост зникає з майстерні, публікація —
    # лишається такою, якою її бачать люди.
    return {"deleted": post_id}


def save_render(db: Session, post_id: int, *, fmt: str, raw: bytes,
                mime: str = "image/png", as_preview: bool = True) -> dict:
    """Прийняти готовий растр із редактора.

    Ключ — content-addressed (як у підбірок): той самий макет не плодить копій
    у хмарі, а зміна макета завжди дає новий URL, повз будь-який кеш CDN.
    """
    if fmt not in CANVAS_FORMATS:
        raise StudioError(f"Невідомий формат полотна: {fmt}")
    if not raw:
        raise StudioError("Порожній растр")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise StudioError("Растр завеликий")
    exists = db.execute(text("SELECT id FROM studio_posts WHERE id = :id"),
                        {"id": post_id}).first()
    if exists is None:
        raise StudioError("Пост не знайдено")

    digest = hashlib.sha256(raw).hexdigest()
    ext = "png" if mime == "image/png" else "jpeg"
    key = f"{R2_RENDER_PREFIX}/{post_id}/{fmt}-{digest[:24]}.{ext}"
    r2 = _r2()
    r2.upload_bytes(raw, key, content_type=mime)
    _write_cached(key, raw)

    try:
        with Image.open(io.BytesIO(raw)) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001 — розмір довідковий, не критичний
        width = CANVAS_FORMATS[fmt]["width"]
        height = CANVAS_FORMATS[fmt]["height"]

    entry = {
        "key": key, "url": r2.public_url(key), "bytes": len(raw),
        "width": width, "height": height, "mime": mime,
    }
    params = {
        "id": post_id, "fmt": fmt, "entry": json.dumps(entry),
        "preview_key": key if as_preview else None,
        "preview_url": entry["url"] if as_preview else None,
    }
    preview_sets = (", preview_key = :preview_key, preview_url = :preview_url"
                    if as_preview else "")
    row = db.execute(text(f"""
        UPDATE studio_posts
        SET renders_json = jsonb_set(
                COALESCE(renders_json, '{{}}'::jsonb),
                ARRAY[:fmt],
                CAST(:entry AS jsonb) || jsonb_build_object(
                    'rendered_at', to_jsonb(now())),
                true){preview_sets},
            updated_at = now()
        WHERE id = :id
        RETURNING id, title, status, base_format, spec_json, targets_json,
                  caption, preview_key, preview_url, renders_json, collection_id,
                  scheduled_at, published_at, created_at, updated_at
    """), params).mappings().one()
    db.commit()
    return _post_row(row)


def preview_bytes(db: Session, post_id: int, fmt: Optional[str] = None) -> Tuple[bytes, str]:
    row = db.execute(
        text("SELECT preview_key, renders_json FROM studio_posts WHERE id = :id"),
        {"id": post_id},
    ).mappings().first()
    if row is None:
        raise StudioError("Пост не знайдено")
    key = None
    mime = "image/png"
    if fmt:
        entry = (row["renders_json"] or {}).get(fmt)
        if isinstance(entry, dict):
            key = entry.get("key")
            mime = entry.get("mime") or mime
    key = key or row["preview_key"]
    if not key:
        raise StudioError("Для цього поста ще немає растру")
    return object_bytes(key), mime
