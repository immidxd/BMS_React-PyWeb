"""Пошук фотографій товару за productnumber.

Зараз: локальна папка (default `~/Downloads/Бізнес/Товар/Взуття`).
Майбутнє: cloud (Google Drive тощо) — реалізується через інший провайдер
з тією ж сигнатурою `list_images(productnumber) -> List[ImageEntry]`.
"""

from __future__ import annotations

import os
import re
import time
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
# Корінь усіх фото товару. Усередині — категорійні підпапки (Взуття, Сумки,
# Одяг, Аксесуари, Інше). Скануємо корінь + усі підпапки одного рівня (так само,
# як Drive-індекс), інакше фото сумок/одягу були б невидимі локально.
DEFAULT_IMAGES_DIR = os.path.expanduser("~/Downloads/Бізнес/Товар")
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
URL_PREFIX = "/product-images"  # match StaticFiles mount in app/main.py


def get_images_dir() -> str:
    return os.environ.get("PRODUCT_IMAGES_DIR", DEFAULT_IMAGES_DIR)


@dataclass
class ImageEntry:
    filename: str
    url: str
    index: int  # порядковий номер у галереї (0 = головна)
    is_defect: bool = False  # фото дефекту (filename типу `<pnum>_defN.<ext>`)
    kind: str = "official"  # 'official' | 'real' | 'defect'
    hidden: bool = False  # прихований від публіки; у картці показується сірим
    # Convention:
    #   <pnum>_NN.<ext>     → official (студійні, для постів)
    #   <pnum>_00NN.<ext>   → real     (мої фото, два нулі на початку)
    #   <pnum>_defN.<ext>   → defect   (показується в обох галереях)


def _file_version(abs_path: str) -> str:
    """`?v=<hash>` із mtime+size для cache-busting при заміні фото. '' якщо нема."""
    try:
        st = os.stat(abs_path)
        # Наносекунди важливі для швидких послідовних поворотів: дві версії
        # одного WebP можуть мати однаковий розмір і потрапити в ту саму секунду.
        return f"?v={st.st_mtime_ns:x}{st.st_size:x}"
    except OSError:
        return ""


def _normalize_number(productnumber: str) -> str:
    """Повертає очищений номер: без `#` префіксу, trim."""
    if not productnumber:
        return ""
    return productnumber.strip().lstrip("#").strip()


def _matches_productnumber(filename: str, target: str) -> bool:
    """Перевірка чи файл належить товару.

    Файл матчить, якщо починається з `target` або `#target`,
    і одразу після номера йде `_`, `.`, пробіл або кінець basename.
    Важливо: дефіс `-` НЕ є розділювачем! Бо `Ф1067-2` — це окремий
    productnumber (варіант/ростовка), не атрибут товару `Ф1067`.
    Так `Ф1067` НЕ матчить `Ф1067-2_01.jpg` (після Ф1067 стоїть `-`).
    """
    if not target:
        return False
    base = os.path.splitext(filename)[0]
    pattern = rf"^#?{re.escape(target)}(?=[_.\s]|$)"
    return bool(re.match(pattern, base, flags=re.IGNORECASE))


def _is_defect_filename(filename: str, target: str) -> bool:
    """Повертає True для файлів типу `<pnum>_def<N>.<ext>` (з опційним `#`).

    Приклади: `Ф4021_def1.jpeg`, `А1248_def2.JPG`, `#М100_def10.png` → True
    """
    if not target:
        return False
    base = os.path.splitext(filename)[0]
    # одразу після номера товару — `_def` + цифри
    pattern = rf"^#?{re.escape(target)}_def\d+\b"
    return bool(re.match(pattern, base, flags=re.IGNORECASE))


def _is_real_filename(filename: str, target: str) -> bool:
    """Реальні фото: `<pnum>_00N.<ext>` — рівно два нулі на початку індексу.

    Приклади: `Ф4021_001.jpg`, `А1248_0012.png` → True
             `Ф4021_01.jpg`,  `Ф4021_def1.jpg` → False
    """
    if not target:
        return False
    base = os.path.splitext(filename)[0]
    pattern = rf"^#?{re.escape(target)}_00\d+\b"
    return bool(re.match(pattern, base, flags=re.IGNORECASE))


def _classify(filename: str, target: str) -> str:
    if _is_defect_filename(filename, target):
        return "defect"
    if _is_real_filename(filename, target):
        return "real"
    return "official"


def _sort_key(filename: str, target: str) -> tuple:
    """Натуральний порядок усередині кожного kind.

    Групи:  0 — official, 1 — official без числа, 2 — real, 3 — defect (в кінці).
    Дефектні фото йдуть в кінець спільної стрічки, щоб у whichever-галереї
    показуватись після всіх «нормальних».
    Tie-breaker: повне ім'я файлу alphabetically.
    """
    base = os.path.splitext(filename)[0]
    # видаляємо префікс # та сам номер товару (case-insensitive — для А1248 → а1248)
    stripped = re.sub(rf"^#?{re.escape(target)}", "", base, flags=re.IGNORECASE)
    kind = _classify(filename, target)
    if kind == "defect":
        m = re.search(r"_def(\d+)", stripped, flags=re.IGNORECASE)
        defect_idx = int(m.group(1)) if m else 0
        return (3, defect_idx, base.lower())
    if kind == "real":
        m = re.search(r"_00(\d+)", stripped, flags=re.IGNORECASE)
        real_idx = int(m.group(1)) if m else 0
        return (2, real_idx, base.lower())
    # official
    m = re.search(r"\d+", stripped)
    if m:
        return (0, int(m.group(0)), base.lower())
    return (1, 0, base.lower())


def _scan_dir_for(directory: str, target: str) -> List[str]:
    """Імена файлів-картинок у `directory` (без рекурсії), що матчать товар."""
    found: List[str] = []
    try:
        for entry in os.scandir(directory):
            if not entry.is_file():
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            if ext not in IMAGE_EXTENSIONS:
                continue
            if _matches_productnumber(entry.name, target):
                found.append(entry.name)
    except OSError as e:
        logger.warning(f"Failed to scan images dir {directory}: {e}")
    return found


def _list_local_only(target: str) -> List[ImageEntry]:
    """Локальні фото (без дедуплікації).

    Сканує корінь + усі категорійні підпапки одного рівня (Взуття, Сумки, …).
    URL зберігає відносний шлях, щоб StaticFiles-маунт на корені віддав файл
    із потрібної підпапки (`/product-images/Сумки/Ф4079_01.JPG`).
    """
    images_dir = get_images_dir()
    if not os.path.isdir(images_dir):
        logger.debug(f"Images dir not found: {images_dir}")
        return []

    # (basename, відносний шлях від кореня) — relpath потрібен для URL.
    matched: List[tuple] = [(fn, fn) for fn in _scan_dir_for(images_dir, target)]
    try:
        for entry in os.scandir(images_dir):
            if entry.is_dir():
                for fn in _scan_dir_for(entry.path, target):
                    matched.append((fn, os.path.join(entry.name, fn)))
    except OSError as e:
        logger.warning(f"Failed to scan subfolders of {images_dir}: {e}")

    # Сортуємо за іменем файлу (натуральний порядок усередині kind).
    matched.sort(key=lambda t: _sort_key(t[0], target))
    return [
        ImageEntry(
            filename=fn,
            # quote зберігає '/' за замовчуванням → шлях лишається валідним.
            # `?v=` (версія за mtime+size) бустить кеш при заміні фото під тією ж
            # назвою — інакше immutable-кеш браузера віддавав би старе.
            url=f"{URL_PREFIX}/{quote(relpath)}{_file_version(os.path.join(images_dir, relpath))}",
            index=i,
            is_defect=_is_defect_filename(fn, target),
            kind=_classify(fn, target),
        )
        for i, (fn, relpath) in enumerate(matched)
    ]


def _list_drive_only(target: str) -> List[ImageEntry]:
    """Drive фото (через провайдер; повертає [] при недоступності API)."""
    try:
        from backend.services.product_images_drive import (
            list_drive_images_for, URL_PREFIX_DRIVE,
        )
    except ImportError:
        try:
            from services.product_images_drive import (
                list_drive_images_for, URL_PREFIX_DRIVE,
            )
        except ImportError:
            return []
    try:
        drive_entries = list_drive_images_for(target)
    except Exception as e:
        logger.warning(f"Drive list failed for {target}: {e}")
        return []
    # Sort using same natural-order key as local
    filenames_sorted = sorted(
        [(e.filename, e.file_id) for e in drive_entries],
        key=lambda x: _sort_key(x[0], target),
    )
    return [
        ImageEntry(
            filename=fn,
            url=f"{URL_PREFIX_DRIVE}/{quote(fid)}",
            index=i,
            is_defect=_is_defect_filename(fn, target),
            kind=_classify(fn, target),
        )
        for i, (fn, fid) in enumerate(filenames_sorted)
    ]


# Повний локальний пошук проходить великий фотоархів і на робочій базі може
# тривати кілька секунд. У межах відкритої картки набір файлів той самий, тому
# кешуємо вже перевірений список за номером. Усі мутації BMS явно скидають цей
# кеш; TTL лишається страховкою для файлів, змінених поза програмою.
_IMAGE_LIST_CACHE_TTL = float(os.environ.get("PRODUCT_IMAGE_LIST_TTL", "120"))
_IMAGE_LIST_CACHE_MAX = int(os.environ.get("PRODUCT_IMAGE_LIST_CACHE_MAX", "512"))
_IMAGE_LIST_CACHE: "OrderedDict[str, tuple[float, List[ImageEntry]]]" = OrderedDict()
_IMAGE_LIST_CACHE_LOCK = threading.Lock()


# ── Приховані знімки ────────────────────────────────────────────────────────
# ЄДИНЕ МІСЦЕ, ДЕ ВИРІШУЄТЬСЯ «показувати чи ні». Через `list_images` проходить
# УСЕ: галерея картки, контент-план, Prom (а з ним OLX і Shafa) і Telegram. Тож
# фільтр стоїть саме тут, а не в кожного споживача — інакше це була б та сама
# дірка, коли ознака врахована в чотирьох місцях із восьми.
#
# ⚠️ Файл при цьому НЕ видаляється ні з диска, ні з R2. Це свідомо: вже
# опубліковані оголошення на маркетплейсах тримаються за URL, і видалення
# показало б там биту картинку замість фото. Приховане просто перестає
# пропонуватись будь-де надалі.
_HIDDEN_TTL = float(os.getenv("PHOTO_HIDDEN_TTL", "30"))
_HIDDEN_CACHE: dict = {"at": 0.0, "keys": frozenset()}
_HIDDEN_LOCK = threading.Lock()


def _hidden_keys(force: bool = False) -> frozenset:
    """{(номер, ім'я файлу)} у нижньому регістрі. Порожньо, якщо БД недоступна."""
    now = time.monotonic()
    with _HIDDEN_LOCK:
        if not force and now - _HIDDEN_CACHE["at"] < _HIDDEN_TTL:
            return _HIDDEN_CACHE["keys"]
    try:
        try:
            from models.database import SessionLocal
        except ImportError:  # pragma: no cover
            from backend.models.database import SessionLocal
        from sqlalchemy import text as _text
        db = SessionLocal()
        try:
            # ⚠️ Опускаємо регістр у PYTHON, а не в SQL. База створена з локаллю
            # C, де `lower()` не чіпає кирилицю: 'Ф4384' лишається 'Ф4384', тоді
            # як Python дає 'ф4384'. Ключі просто ніколи не збігались, і
            # приховування мовчки не діяло.
            rows = db.execute(_text(
                "SELECT productnumber, filename FROM product_photo_hidden"
            )).fetchall()
        finally:
            db.close()
        keys = frozenset(((a or "").strip().lower(), (b or "").strip().lower())
                         for a, b in rows)
    except Exception as exc:  # noqa: BLE001
        # ⚠️ Помилка БД НЕ має ховати всі фото — це зробило б збій бази
        # видимим покупцям. Порожній набір означає «нічого не приховано».
        logger.warning("Не вдалось прочитати приховані фото: %s", exc)
        keys = frozenset()
    with _HIDDEN_LOCK:
        _HIDDEN_CACHE["at"] = now
        _HIDDEN_CACHE["keys"] = keys
    return keys


def is_hidden(productnumber: str, filename: str) -> bool:
    key = (_normalize_number(productnumber).lower(), (filename or "").strip().lower())
    return key in _hidden_keys()


def invalidate_hidden_cache() -> None:
    """Скинути кеш прихованих — після приховування/повернення."""
    with _HIDDEN_LOCK:
        _HIDDEN_CACHE["at"] = 0.0


def invalidate_image_list_cache(*productnumbers: str) -> None:
    """Скинути кеш конкретних номерів; без аргументів — увесь кеш списків."""
    with _IMAGE_LIST_CACHE_LOCK:
        if not productnumbers:
            _IMAGE_LIST_CACHE.clear()
            return
        for productnumber in productnumbers:
            key = _normalize_number(productnumber).lower()
            if key:
                _IMAGE_LIST_CACHE.pop(key, None)


def list_images(productnumber: str, include_hidden: bool = False) -> List[ImageEntry]:
    """Об'єднаний список фото товару (локально + Drive) з дедуплікацією за filename.

    Принцип:
      • Локальне ВИГРАЄ при колізії — швидший доступ, без quota.
      • Drive додається тільки для тих filename-ів, яких НЕ було локально.
      • Якщо локальної папки немає (інший комп) — буде лише Drive автоматично.
      • Сортування — натуральне за суфіксним числом → головне фото index=0.

    `include_hidden=True` віддає й приховані (з `hidden=True`) — це потрібно
    РІВНО одному споживачу, галереї картки, щоб показати їх сірими. Усі інші
    шляхи лишаються з типовим False і приховане не бачать.
    """
    target = _normalize_number(productnumber)
    if not target:
        return []

    cache_key = target.lower()
    now = time.monotonic()
    with _IMAGE_LIST_CACHE_LOCK:
        cached = _IMAGE_LIST_CACHE.get(cache_key)
        if cached and now - cached[0] < _IMAGE_LIST_CACHE_TTL:
            _IMAGE_LIST_CACHE.move_to_end(cache_key)
            # Не віддаємо сам кешований list: споживач може сортувати/обрізати
            # його локально, але не повинен змінити наступний lookup.
            # ⚠️ Кеш тримає ПОВНИЙ список із прапорцями, а відсів іде на виході:
            # інакше довелось би тримати два кеші, і вони б розійшлися.
            return _apply_hidden(target, cached[1], include_hidden)
        if cached:
            _IMAGE_LIST_CACHE.pop(cache_key, None)

    local = _list_local_only(target)
    drive = _list_drive_only(target)

    seen_lower = {e.filename.lower() for e in local}
    merged: List[ImageEntry] = list(local)
    for e in drive:
        if e.filename.lower() not in seen_lower:
            seen_lower.add(e.filename.lower())
            merged.append(e)

    # Resort merged via natural order on filename
    merged_sorted = sorted(
        merged, key=lambda e: _sort_key(e.filename, target)
    )
    # Re-index sequentially (0..N) on the merged result
    result = [
        ImageEntry(filename=e.filename, url=e.url, index=i, is_defect=e.is_defect,
                   kind=e.kind, hidden=e.hidden)
        for i, e in enumerate(merged_sorted)
    ]
    with _IMAGE_LIST_CACHE_LOCK:
        _IMAGE_LIST_CACHE[cache_key] = (now, result)
        _IMAGE_LIST_CACHE.move_to_end(cache_key)
        while len(_IMAGE_LIST_CACHE) > max(1, _IMAGE_LIST_CACHE_MAX):
            _IMAGE_LIST_CACHE.popitem(last=False)
    return _apply_hidden(target, result, include_hidden)


def _apply_hidden(target: str, entries: List[ImageEntry],
                  include_hidden: bool) -> List[ImageEntry]:
    """Проставити `hidden` і, якщо не просили інакше, прибрати приховані.

    Індекси перераховуються ПІСЛЯ відсіву: споживачі беруть «перше фото» за
    index=0, і приховане головне не має лишати діру на початку стрічки.
    """
    hidden = _hidden_keys()
    key = target.lower()
    marked = [
        ImageEntry(filename=e.filename, url=e.url, index=e.index, is_defect=e.is_defect,
                   kind=e.kind, hidden=(key, e.filename.lower()) in hidden)
        for e in entries
    ]
    if not include_hidden:
        marked = [e for e in marked if not e.hidden]
    from dataclasses import replace as _replace
    return [_replace(e, index=i) for i, e in enumerate(marked)]


def read_image_bytes(entry: ImageEntry) -> Optional[bytes]:
    """Байти одного фото за його `url` (локальний файл або Drive-проксі).

    Потрібно для пакетного експорту (zip): віддавати фото без походу браузера
    по кожному URL окремо. `None`, якщо файл недоступний.
    """
    url = (entry.url or "").split("?")[0]

    if url.startswith(URL_PREFIX + "/"):
        rel = unquote(url[len(URL_PREFIX) + 1:])
        root = os.path.abspath(get_images_dir())
        abs_path = os.path.abspath(os.path.join(root, rel))
        # Захист від виходу за корінь (relpath приходить із нашого ж лістингу,
        # але шлях будується з рядка — перевіряємо явно).
        if not (abs_path == root or abs_path.startswith(root + os.sep)):
            logger.warning(f"Refusing to read outside images dir: {abs_path}")
            return None
        try:
            with open(abs_path, "rb") as f:
                return f.read()
        except OSError as e:
            logger.warning(f"Failed to read {abs_path}: {e}")
            return None

    # Drive: /product-images-drive/<file_id>
    try:
        from backend.services.product_images_drive import (
            get_drive_file_bytes, URL_PREFIX_DRIVE,
        )
    except ImportError:
        try:
            from services.product_images_drive import (
                get_drive_file_bytes, URL_PREFIX_DRIVE,
            )
        except ImportError:
            return None
    if url.startswith(URL_PREFIX_DRIVE + "/"):
        file_id = unquote(url[len(URL_PREFIX_DRIVE) + 1:])
        try:
            result = get_drive_file_bytes(file_id)
        except Exception as e:
            logger.warning(f"Drive fetch failed for {file_id}: {e}")
            return None
        return result[0] if result else None
    return None


# ── Масовий індикатор «чи є фото» для списку товарів ───────────────────────────
# Сканувати папку/Drive на КОЖЕН рядок (3000+ товарів) надто дорого. Замість
# цього один раз будуємо множину «номерів, що мають ≥1 фото» і кешуємо її.
# Ключ = провідний токен імені файлу (до першого `_`/`.`/пробілу, `-` лишається
# частиною номера) у lowercase — узгоджено з матчингом `list_images`, тож
# `pnum.lower() in set` ⇔ list_images(pnum) поверне ≥1 фото (для звичайних
# номерів без внутрішніх пробілів).
_PHOTO_SET_CACHE: dict = {"set": frozenset(), "ts": 0.0, "valid": False}
_PHOTO_SET_TTL = float(os.environ.get("PRODUCT_PHOTO_SET_TTL", "300"))


def _pnum_token_from_filename(filename: str) -> Optional[str]:
    """Провідний токен номера з імені файлу (lowercase). `-` НЕ розділювач."""
    base = os.path.splitext(filename)[0]
    m = re.match(r"^#?([^\s_.#]+)", base)
    return m.group(1).strip().lower() if m else None


def get_photo_pnum_set(force: bool = False) -> frozenset:
    """Множина normalized-lowercase номерів, що мають ≥1 фото (локально ∪ Drive).
    Кешується на `PRODUCT_PHOTO_SET_TTL` сек. Drive береться лише з вже-прогрітого
    індексу (без блокуючого скану), тож виклик у списку товарів дешевий."""
    now = time.time()
    if not force and _PHOTO_SET_CACHE["valid"] and (now - _PHOTO_SET_CACHE["ts"]) < _PHOTO_SET_TTL:
        return _PHOTO_SET_CACHE["set"]

    result: set = set()
    images_dir = get_images_dir()
    if os.path.isdir(images_dir):
        scan_dirs = [images_dir]
        try:
            for entry in os.scandir(images_dir):
                if entry.is_dir():
                    scan_dirs.append(entry.path)
        except OSError as e:
            logger.warning(f"photo-set: failed to list subfolders of {images_dir}: {e}")
        for d in scan_dirs:
            try:
                for entry in os.scandir(d):
                    if not entry.is_file():
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in IMAGE_EXTENSIONS:
                        continue
                    tok = _pnum_token_from_filename(entry.name)
                    if tok:
                        result.add(tok)
            except OSError:
                continue

    # Drive — лише вже закешований індекс (не форсимо скан).
    try:
        from backend.services.product_images_drive import get_cached_drive_pnums
    except ImportError:
        try:
            from services.product_images_drive import get_cached_drive_pnums
        except ImportError:
            get_cached_drive_pnums = None  # type: ignore
    if get_cached_drive_pnums is not None:
        try:
            result.update(get_cached_drive_pnums())
        except Exception as e:
            logger.debug(f"photo-set: drive pnums unavailable: {e}")

    frozen = frozenset(result)
    _PHOTO_SET_CACHE.update(set=frozen, ts=now, valid=True)
    return frozen


# Окремий кеш для студійних: множина інша, а TTL і спосіб побудови ті самі.
_OFFICIAL_SET_CACHE: dict = {"set": frozenset(), "ts": 0.0, "valid": False}


def get_official_photo_pnum_set(force: bool = False) -> frozenset:
    """Множина номерів, що мають ≥1 СТУДІЙНЕ фото (`<pnum>_NN`).

    Відрізняється від `get_photo_pnum_set` рівно одним: `_00N` (реальні, «як є»)
    і `_defN` (дефекти) не рахуються. Потрібна там, де знімок іде у відкриту
    вітрину й домашнє фото на килимі не годиться, — автоматичні Stories.

    ⚠️ Це ДЕШЕВИЙ переднабір для SQL, а не остаточний вердикт: ключем тут
    служить провідний токен імені файлу, а `list_images` матчить трохи ширше
    (номери з пробілами). Тому множина може віддати зайвий номер — і саме тому
    в добірці за нею йде жива перевірка `kind` по конкретному товару.
    """
    now = time.time()
    if not force and _OFFICIAL_SET_CACHE["valid"] and (now - _OFFICIAL_SET_CACHE["ts"]) < _PHOTO_SET_TTL:
        return _OFFICIAL_SET_CACHE["set"]

    result: set = set()
    images_dir = get_images_dir()
    if os.path.isdir(images_dir):
        scan_dirs = [images_dir]
        try:
            for entry in os.scandir(images_dir):
                if entry.is_dir():
                    scan_dirs.append(entry.path)
        except OSError as e:
            logger.warning(f"official-photo-set: failed to list subfolders of {images_dir}: {e}")
        for d in scan_dirs:
            try:
                for entry in os.scandir(d):
                    if not entry.is_file():
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in IMAGE_EXTENSIONS:
                        continue
                    tok = _pnum_token_from_filename(entry.name)
                    if tok and _classify(entry.name, tok) == "official":
                        result.add(tok)
            except OSError:
                continue

    # Drive — лише вже прогрітий індекс, як і в `get_photo_pnum_set`.
    try:
        from backend.services.product_images_drive import get_cached_drive_official_pnums
    except ImportError:
        try:
            from services.product_images_drive import get_cached_drive_official_pnums
        except ImportError:
            get_cached_drive_official_pnums = None  # type: ignore
    if get_cached_drive_official_pnums is not None:
        try:
            result.update(get_cached_drive_official_pnums())
        except Exception as e:
            logger.debug(f"official-photo-set: drive pnums unavailable: {e}")

    frozen = frozenset(result)
    _OFFICIAL_SET_CACHE.update(set=frozen, ts=now, valid=True)
    return frozen


def product_has_photo(productnumber: Optional[str], photo_set: Optional[frozenset] = None) -> bool:
    """Чи має товар фото (за номером). `photo_set` можна передати ззовні, щоб не
    тягати кеш на кожен рядок."""
    if not productnumber:
        return False
    key = productnumber.strip().lstrip("#").strip().lower()
    if not key:
        return False
    ps = photo_set if photo_set is not None else get_photo_pnum_set()
    return key in ps
