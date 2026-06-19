"""Керування ОФІЦІЙНИМИ фото товару (для менеджера в картці).

Операції над студійними фото `<pnum>_NN.webp` у локальному міорі + синхронізація
з Cloudflare R2 (той самий ключ `<категорія>/<pnum>_NN.webp`). Реальні фото
(`_00NN`, з Drive) і дефекти (`_defN`) НЕ чіпаємо — це окремі набори.

Принцип: локальний мірор = майстер; кожна зміна одразу віддзеркалюється в R2.
Іменування завжди нормалізоване: `<pnum>_01.webp`, `_02`, … (перше = головне).
Перенумерація замість «перейменування»: користувач тягне порядок, ми проставляємо
індекси. Кеш не заважає (нові індекси = нові ключі/URL).
"""

from __future__ import annotations

import os
import re
import uuid
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from backend.services import r2_storage
    from backend.scripts.ingest_photos import convert_to_webp_master
except ImportError:  # запуск з backend/
    from services import r2_storage  # type: ignore
    from scripts.ingest_photos import convert_to_webp_master  # type: ignore

MIRROR_ROOT = Path(os.environ.get(
    "PRODUCT_IMAGES_DIR", os.path.expanduser("~/Downloads/Бізнес/Товар")))
VALID_CATEGORIES = ["Взуття", "Сумки", "Одяг", "Аксесуари", "Інше"]

# Тип товару (lowercase) → папка-категорія. Фолбек — «Інше».
_TYPE_CATEGORY = {
    **{t: "Взуття" for t in (
        "кросівки", "кросовки", "ботинки", "босоніжки", "шльопанці", "туфлі",
        "мокасини", "сапоги", "кеди", "напівсапоги", "напівботинки", "тапки",
        "балетки", "сліпони", "лофери", "сабо", "взуття", "черевики", "уги", "угі")},
    **{t: "Сумки" for t in (
        "сумка", "cумка", "рюкзак", "валіза", "гаманець", "клатч", "барсетка")},
    **{t: "Одяг" for t in (
        "куртка", "білизна", "піжама", "штани", "сукня", "футболка", "светр",
        "кофта", "пальто", "джинси", "шорти", "спідниця", "комбінезон", "худі",
        "толстовка", "сорочка", "блуза", "жилет", "кардиган")},
    **{t: "Аксесуари" for t in (
        "ремінь", "пасок", "шапка", "шкарпетки", "рукавиці", "шарф", "окуляри")},
}

_OFFICIAL_RE = lambda pnum: re.compile(  # noqa: E731
    rf"^#?{re.escape(pnum)}_(\d{{1,2}})$", re.IGNORECASE)


def _norm(pnum: str) -> str:
    return (pnum or "").strip().lstrip("#").strip()


def resolve_category(pnum: str, type_name: Optional[str] = None) -> str:
    """Папка для фото товару: де вже лежать його фото → інакше за типом → «Інше»."""
    pn = _norm(pnum)
    for cat in VALID_CATEGORIES:
        d = MIRROR_ROOT / cat
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and _OFFICIAL_RE(pn).match(f.stem):
                    return cat
    if type_name:
        return _TYPE_CATEGORY.get(type_name.strip().lower(), "Інше")
    return "Інше"


def _official_files(pnum: str, category: str) -> List[Path]:
    """Офіційні webp файли товару в категорії, відсортовані за індексом."""
    pn = _norm(pnum)
    d = MIRROR_ROOT / category
    if not d.is_dir():
        return []
    out = []
    for f in d.iterdir():
        if not f.is_file() or f.suffix.lower() != ".webp":
            continue
        m = _OFFICIAL_RE(pn).match(f.stem)
        if m:
            out.append((int(m.group(1)), f))
    return [f for _, f in sorted(out, key=lambda t: t[0])]


def _r2_key(category: str, filename: str) -> str:
    return f"{category}/{filename}"


def _sync_one(category: str, path: Path):
    """Залити один файл у R2 (якщо R2 увімкнено)."""
    if r2_storage.is_enabled():
        r2_storage.upload_file(str(path), _r2_key(category, path.name))


def _delete_r2(category: str, filename: str):
    if r2_storage.is_enabled():
        key = _r2_key(category, filename)
        try:
            if r2_storage.object_exists(key):
                r2_storage.delete(key)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"R2 delete fail {key}: {e}")


def add_photos(pnum: str, category: str, sources: List[tuple]) -> int:
    """Додати офіційні фото. sources = [(tmp_path, _orig_name), …].
    Конвертує у WebP-майстер, нумерує з наступного вільного індексу, синкає R2."""
    pn = _norm(pnum)
    existing = _official_files(pn, category)
    next_idx = 1
    if existing:
        m = _OFFICIAL_RE(pn).match(existing[-1].stem)
        next_idx = (int(m.group(1)) if m else len(existing)) + 1
    (MIRROR_ROOT / category).mkdir(parents=True, exist_ok=True)
    added = 0
    for tmp_path, _orig in sources:
        name = f"{pn}_{next_idx:02d}.webp"
        dest = MIRROR_ROOT / category / name
        convert_to_webp_master(Path(tmp_path), dest)
        _sync_one(category, dest)
        next_idx += 1
        added += 1
    return added


def delete_photo(pnum: str, category: str, filename: str) -> bool:
    """Видалити одне офіційне фото (мірор + R2)."""
    pn = _norm(pnum)
    if not _OFFICIAL_RE(pn).match(Path(filename).stem):
        raise ValueError("Можна видаляти лише офіційні фото цього товару")
    path = MIRROR_ROOT / category / filename
    if path.exists():
        path.unlink()
    _delete_r2(category, filename)
    return True


def replace_photo(pnum: str, category: str, filename: str, tmp_path: str) -> str:
    """Замінити ВМІСТ одного офіційного фото (та сама назва/позиція, новий файл).
    Конвертує новий файл у WebP під тим самим іменем, перезаливає R2. Cache-busting
    робить `?v=` у URL (mtime зміниться) — старе фото оновиться всюди."""
    pn = _norm(pnum)
    if not _OFFICIAL_RE(pn).match(Path(filename).stem):
        raise ValueError("Можна замінювати лише офіційні фото цього товару")
    dest = MIRROR_ROOT / category / filename
    convert_to_webp_master(Path(tmp_path), dest)  # перезаписує під тим самим іменем
    _sync_one(category, dest)                      # та сама R2-ключ, новий вміст
    return filename


def reorder_photos(pnum: str, category: str, ordered_filenames: List[str]) -> List[str]:
    """Перенумерувати офіційні фото у вказаному порядку → `_01.._0N`.
    Двофазне перейменування (через тимчасові імена) уникає колізій. Синкає R2."""
    pn = _norm(pnum)
    cat_dir = MIRROR_ROOT / category
    current = {f.name for f in _official_files(pn, category)}
    ordered = [fn for fn in ordered_filenames if fn in current]
    if set(ordered) != current:
        raise ValueError("Список для перестановки не збігається з наявними фото")

    # Фаза 1: усе → тимчасові імена (щоб не зіткнутись із цільовими)
    tmp_map = []  # (tmp_path, target_name)
    for i, old_name in enumerate(ordered, start=1):
        target = f"{pn}_{i:02d}.webp"
        tmp = cat_dir / f"__tmp_{uuid.uuid4().hex}.webp"
        (cat_dir / old_name).rename(tmp)
        tmp_map.append((tmp, target))
    # Фаза 2: тимчасові → фінальні + залив усіх фінальних у R2
    result = []
    for tmp, target in tmp_map:
        tmp.rename(cat_dir / target)
        result.append(target)
        _sync_one(category, cat_dir / target)
    # Прибрати з R2 лише СПРАВЖНІХ сиріт (старі імена, яких нема серед нових —
    # напр. коли закрили прогалину _01,_03 → _01,_02). У чистій перестановці
    # old==new, тож нічого не видаляється (інакше затерли б щойно залите).
    for orphan in set(ordered) - set(result):
        _delete_r2(category, orphan)
    return result
