"""Шрифти, встановлені на пристрої — як джерело для майстерні.

Навіщо окремий модуль. Браузер не бачить системних шрифтів: Local Font Access
API є лише в Chrome, а програма живе у WKWebView. Зате бекенд — це звичайний
Python на тій самій машині, і теки зі шрифтами йому доступні. Тому каталог
збирає він.

Головне рішення: обраний шрифт **копіюється в майстерню** (у хмару), а не
використовується «за посиланням на файл». Так макет лишається відтворюваним:
кадр збереться однаково і на іншому комп'ютері, і в майбутньому хмарному
рендері, де жодного `/System/Library/Fonts` немає.

`.ttc` (а це половина системних шрифтів macOS — Avenir, Futura, Helvetica)
браузер не вміє. Тому потрібну гарнітуру витягуємо з колекції в окремий файл
через fontTools. Без fontTools модуль не падає: залишає лише `.ttf`/`.otf`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from services import studio
except ImportError:  # запуск із кореня репо
    from backend.services import studio  # type: ignore

try:
    from fontTools.ttLib import TTCollection, TTFont
    FONTTOOLS = True
except ImportError:  # pragma: no cover — середовище без fontTools
    FONTTOOLS = False

# Теки в порядку «своє спершу»: власні шрифти користувача цікавлять його
# найбільше, і саме вони мають бути вгорі списку.
FONT_DIRS: Tuple[str, ...] = (
    os.path.expanduser("~/Library/Fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/System/Library/Fonts",
    # Windows-збірка BMS ставиться на інші машини — хай каталог працює і там.
    os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
    os.path.expanduser("~/.fonts"),
    "/usr/share/fonts",
)

SCANNABLE = (".ttf", ".otf", ".ttc", ".otc")
CACHE_FILE = os.path.join(studio.CACHE_DIR, "system-fonts.json")
CACHE_VERSION = 1

_WEIGHT_NAMES = {
    100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
    600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black",
}


def _face_token(path: str, index: int) -> str:
    """Стабільний ідентифікатор гарнітури для фронта.

    Шлях усередині — щоб імпорт не мусив довіряти довільному шляху з мережі:
    він приймає токен і сам розкодовує його назад, а далі ще й звіряє теку.
    """
    raw = f"{path}::{index}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_token(token: str) -> Tuple[str, int]:
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode(token + padding).decode("utf-8")
        path, _, index = raw.rpartition("::")
        return path, int(index)
    except Exception as exc:  # noqa: BLE001
        raise studio.StudioError("Невідомий шрифт у запиті") from exc


def _dir_signature() -> str:
    """Відбиток стану тек. Змінився — кеш протух, і це помітно одразу після
    того, як людина встановила новий шрифт у систему."""
    parts: List[str] = []
    for directory in FONT_DIRS:
        try:
            stat = os.stat(directory)
            parts.append(f"{directory}:{int(stat.st_mtime)}:{len(os.listdir(directory))}")
        except OSError:
            parts.append(f"{directory}:-")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _read_faces(path: str) -> List[dict]:
    """Гарнітури одного файлу. Помилка читання — не привід ховати весь шрифт:
    повертаємо хоча б те, що видно з імені файлу."""
    extension = os.path.splitext(path)[1].lower()
    fallback = [{
        "family": os.path.splitext(os.path.basename(path))[0].split("-")[0].strip(),
        "subfamily": "Regular", "weight": 400, "italic": False,
        "index": 0, "has_cyrillic": None,
    }]
    if not FONTTOOLS:
        return fallback if extension in (".ttf", ".otf") else []

    try:
        if extension in (".ttc", ".otc"):
            fonts = TTCollection(path, lazy=False).fonts
        else:
            fonts = [TTFont(path, lazy=True)]
    except Exception as exc:  # noqa: BLE001
        logger.debug("studio: %s не прочитано (%s)", path, exc)
        return fallback if extension in (".ttf", ".otf") else []

    faces: List[dict] = []
    for index, font in enumerate(fonts):
        try:
            names = font["name"]
            family = (names.getDebugName(16) or names.getDebugName(1) or "").strip()
            subfamily = (names.getDebugName(17) or names.getDebugName(2) or "Regular").strip()
            weight = int(getattr(font.get("OS/2"), "usWeightClass", 400) or 400)
            italic = bool(font["head"].macStyle & 2)
            cyrillic = None
            try:
                # Ї (U+0407) — найдешевша перевірка «чи можна цим писати
                # українською»: у латинських шрифтах її майже ніколи немає.
                cyrillic = any(
                    0x0407 in table.cmap
                    for table in font["cmap"].tables if table.isUnicode()
                )
            except Exception:  # noqa: BLE001
                pass
            if not family:
                continue
            faces.append({
                "family": family, "subfamily": subfamily,
                "weight": max(100, min(900, weight)),
                "italic": italic, "index": index, "has_cyrillic": cyrillic,
            })
        except Exception:  # noqa: BLE001
            continue
        finally:
            try:
                font.close()
            except Exception:  # noqa: BLE001
                pass
    return faces or fallback


def _scan() -> dict:
    families: Dict[str, dict] = {}
    for directory in FONT_DIRS:
        if not os.path.isdir(directory):
            continue
        source = "user" if directory.startswith(os.path.expanduser("~")) else "system"
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in entries:
            path = os.path.join(directory, name)
            extension = os.path.splitext(name)[1].lower()
            if extension not in SCANNABLE or not os.path.isfile(path):
                continue
            for face in _read_faces(path):
                family = face["family"]
                bucket = families.setdefault(family, {
                    "family": family, "source": source, "faces": [],
                    "has_cyrillic": False,
                })
                # Той самий шрифт трапляється і в системній теці, і в
                # користувацькій — друга копія лише засмічує список.
                signature = (face["weight"], face["italic"], face["subfamily"])
                if any((item["weight"], item["italic"], item["subfamily"]) == signature
                       for item in bucket["faces"]):
                    continue
                bucket["faces"].append({
                    "token": _face_token(path, face["index"]),
                    "subfamily": face["subfamily"],
                    "weight": face["weight"],
                    "weight_label": _WEIGHT_NAMES.get(face["weight"], str(face["weight"])),
                    "italic": face["italic"],
                    "has_cyrillic": face["has_cyrillic"],
                    "format": extension.lstrip("."),
                })
                if face["has_cyrillic"]:
                    bucket["has_cyrillic"] = True
                if source == "user":
                    bucket["source"] = "user"

    ordered = sorted(
        families.values(),
        # Спершу свої, далі — ті, якими можна писати українською.
        key=lambda item: (item["source"] != "user", not item["has_cyrillic"],
                          item["family"].lower()),
    )
    for item in ordered:
        item["faces"].sort(key=lambda face: (face["weight"], face["italic"]))
    return {"families": ordered, "scanned_at": time.time(),
            "signature": _dir_signature(), "version": CACHE_VERSION,
            "fonttools": FONTTOOLS}


def catalogue(*, refresh: bool = False) -> dict:
    """Каталог шрифтів пристрою. Читання ~900 гарнітур займає секунди, тому
    результат лежить у кеші й перечитується лише коли теки змінились."""
    signature = _dir_signature()
    if not refresh:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if (cached.get("signature") == signature
                    and cached.get("version") == CACHE_VERSION):
                return cached
        except (OSError, ValueError):
            pass
    data = _scan()
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError as exc:
        logger.debug("studio: каталог шрифтів не закешовано: %s", exc)
    return data


def _extract(path: str, index: int) -> Tuple[bytes, str]:
    """Байти однієї гарнітури + розширення, придатне для браузера."""
    extension = os.path.splitext(path)[1].lower()
    if extension in (".ttf", ".otf"):
        with open(path, "rb") as handle:
            return handle.read(), extension.lstrip(".")
    if not FONTTOOLS:
        raise studio.StudioError(
            "Цей шрифт лежить у колекції .ttc — щоб дістати з неї гарнітуру, "
            "потрібен пакет fonttools"
        )
    collection = TTCollection(path, lazy=False)
    if index >= len(collection.fonts):
        raise studio.StudioError("У колекції немає такої гарнітури")
    font = collection.fonts[index]
    buffer = io.BytesIO()
    font.save(buffer)
    # CFF усередині = це OpenType, і віддавати його як font/ttf нечесно:
    # частина рушіїв дивиться саме на тип, а не на вміст.
    suffix = "otf" if "CFF " in font else "ttf"
    return buffer.getvalue(), suffix


def import_face(db, token: str) -> dict:
    """Перенести гарнітуру з пристрою в майстерню (тобто у хмару)."""
    path, index = _decode_token(token)
    real = os.path.realpath(path)
    if not any(real.startswith(os.path.realpath(directory))
               for directory in FONT_DIRS if os.path.isdir(directory)):
        # Токен приходить із мережі. Навіть у локальній програмі не варто
        # дозволяти йому вказувати на будь-який файл у системі.
        raise studio.StudioError("Шрифт поза теками шрифтів пристрою")
    if not os.path.isfile(real):
        raise studio.StudioError("Файл шрифта зник — оновіть каталог")

    faces = _read_faces(real)
    face = next((item for item in faces if item["index"] == index), None)
    if face is None:
        raise studio.StudioError("Гарнітуру не знайдено у файлі")

    raw, suffix = _extract(real, index)
    filename = f"{face['family']}-{face['subfamily']}".replace(" ", "") + f".{suffix}"
    return studio.add_font(
        db, filename=filename, raw=raw, family=face["family"],
        weight=face["weight"], style="italic" if face["italic"] else "normal",
    )
