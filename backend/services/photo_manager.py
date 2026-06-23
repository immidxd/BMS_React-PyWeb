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
# Реальні фото: `<pnum>_00N` — індекс ПІСЛЯ префікса `_00` (1,2,…,10,11 → _001,_002,_0010).
_REAL_RE = lambda pnum: re.compile(  # noqa: E731
    rf"^#?{re.escape(pnum)}_00(\d+)$", re.IGNORECASE)
# Дефекти: `<pnum>_defN` (1-індексовані, без паддінгу → _def1, _def2, …).
_DEFECT_RE = lambda pnum: re.compile(  # noqa: E731
    rf"^#?{re.escape(pnum)}_def(\d+)$", re.IGNORECASE)

# Підтримувані набори фото в мірорі (керовані менеджером картки).
PHOTO_KINDS = ("official", "real", "defect")


def _kind_re(pnum: str, kind: str):
    """Регулярка індексу для набору фото."""
    if kind == "real":
        return _REAL_RE(pnum)
    if kind == "defect":
        return _DEFECT_RE(pnum)
    return _OFFICIAL_RE(pnum)


def _kind_filename(pnum: str, kind: str, idx: int) -> str:
    """Ім'я файлу за набором та індексом (узгоджено з _kind_re і product_images._classify).
    official → `_NN` (паддінг 2); real → `_00{idx}`; defect → `_def{idx}`."""
    if kind == "real":
        return f"{pnum}_00{idx}.webp"      # _001, _002, …, _0010, _0011
    if kind == "defect":
        return f"{pnum}_def{idx}.webp"     # _def1, _def2, …
    return f"{pnum}_{idx:02d}.webp"        # _01, _02, …, _99


def _norm(pnum: str) -> str:
    return (pnum or "").strip().lstrip("#").strip()


def resolve_category(pnum: str, type_name: Optional[str] = None) -> str:
    """Папка для фото товару: де вже лежать його фото → інакше за типом → «Інше».

    ⚠️ Враховуємо фото БУДЬ-ЯКОГО набору (official/real/defect), а не лише
    офіційні. Інакше товар, у якого є лише реальні фото (`_00N`), не «знаходив»
    своєї папки → resolve падав на тип, і delete/replace/reorder/move дивились у
    не ту категорію (фото мовчки не видалялись). Лістинг сканує всі підпапки, тож
    показ працював — а ось операції над файлом ні."""
    pn = _norm(pnum)
    for cat in VALID_CATEGORIES:
        d = MIRROR_ROOT / cat
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and any(_kind_re(pn, k).match(f.stem) for k in PHOTO_KINDS):
                    return cat
    if type_name:
        return _TYPE_CATEGORY.get(type_name.strip().lower(), "Інше")
    return "Інше"


def _official_files(pnum: str, category: str) -> List[Path]:
    """Офіційні webp файли товару в категорії, відсортовані за індексом."""
    return _kind_files(pnum, category, "official")


def _kind_files(pnum: str, category: str, kind: str) -> List[Path]:
    """webp-файли товару в категорії потрібного `kind` ('official'|'real'|'defect'),
    відсортовані за числовим індексом."""
    pn = _norm(pnum)
    d = MIRROR_ROOT / category
    if not d.is_dir():
        return []
    rx = _kind_re(pn, kind)
    out = []
    for f in d.iterdir():
        if not f.is_file() or f.suffix.lower() != ".webp":
            continue
        m = rx.match(f.stem)
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


def _next_index(pnum: str, category: str, kind: str) -> int:
    """Наступний вільний індекс у наборі (за останнім наявним файлом)."""
    pn = _norm(pnum)
    existing = _kind_files(pn, category, kind)
    if not existing:
        return 1
    m = _kind_re(pn, kind).match(existing[-1].stem)
    return (int(m.group(1)) if m else len(existing)) + 1


def add_photos(pnum: str, category: str, sources: List[tuple], kind: str = "official") -> dict:
    """Додати фото потрібного типу. sources = [(tmp_path, _orig_name), …].
    official → `_NN`; real → `_00N`; defect → `_defN`.
    Конвертує у WebP-майстер, нумерує з наступного вільного індексу, синкає R2.

    Стійкий по-файлово: битий/непідтримуваний файл (напр. HEIC без pillow-heif)
    НЕ валить увесь батч — інші зберігаються, а збій повертається у `errors`.
    Повертає {"added": <скільки збережено>, "errors": [{"file": ім'я, "reason": …}]}.
    """
    if kind not in PHOTO_KINDS:
        raise ValueError(f"Невідомий kind: {kind!r}")
    pn = _norm(pnum)
    next_idx = _next_index(pn, category, kind)
    (MIRROR_ROOT / category).mkdir(parents=True, exist_ok=True)
    added = 0
    errors: List[dict] = []
    for tmp_path, _orig in sources:
        name = _kind_filename(pn, kind, next_idx)
        dest = MIRROR_ROOT / category / name
        try:
            convert_to_webp_master(Path(tmp_path), dest)
            _sync_one(category, dest)
        except Exception as e:  # noqa: BLE001 — один файл не має валити весь батч
            if dest.exists():
                try: dest.unlink()
                except OSError: pass
            logger.warning(f"add_photos: не вдалося додати {_orig!r}: {e}")
            errors.append({"file": _orig or name, "reason": str(e)})
            continue
        next_idx += 1
        added += 1
    return {"added": added, "errors": errors}


def delete_photo(pnum: str, category: str, filename: str) -> bool:
    """Видалити одне фото (official/real/defect) цього товару (мірор + R2)."""
    pn = _norm(pnum)
    stem = Path(filename).stem
    if not any(_kind_re(pn, k).match(stem) for k in PHOTO_KINDS):
        raise ValueError("Можна видаляти лише фото цього товару (official або real)")
    path = MIRROR_ROOT / category / filename
    if path.exists():
        path.unlink()
    _delete_r2(category, filename)
    return True


def replace_photo(pnum: str, category: str, filename: str, tmp_path: str) -> str:
    """Замінити ВМІСТ одного фото (та сама назва/позиція, новий файл).
    Працює і для official, і для real. Cache-busting робить `?v=` у URL."""
    pn = _norm(pnum)
    stem = Path(filename).stem
    if not (_OFFICIAL_RE(pn).match(stem) or _REAL_RE(pn).match(stem)):
        raise ValueError("Можна замінювати лише фото цього товару (official або real)")
    dest = MIRROR_ROOT / category / filename
    convert_to_webp_master(Path(tmp_path), dest)  # перезаписує під тим самим іменем
    _sync_one(category, dest)                      # та сама R2-ключ, новий вміст
    return filename


def reorder_photos(pnum: str, category: str, ordered_filenames: List[str], kind: str = "official") -> List[str]:
    """Перенумерувати фото у вказаному порядку (official→`_0N`, real→`_00N`, defect→`_defN`).
    Двофазне перейменування (через тимчасові імена) уникає колізій. Синкає R2."""
    if kind not in PHOTO_KINDS:
        raise ValueError(f"Невідомий kind: {kind!r}")
    pn = _norm(pnum)
    cat_dir = MIRROR_ROOT / category
    current = {f.name for f in _kind_files(pn, category, kind)}
    ordered = [fn for fn in ordered_filenames if fn in current]
    if set(ordered) != current:
        raise ValueError("Список для перестановки не збігається з наявними фото")

    # Фаза 1: усе → тимчасові імена (щоб не зіткнутись із цільовими)
    tmp_map = []  # (tmp_path, target_name)
    for i, old_name in enumerate(ordered, start=1):
        target = _kind_filename(pn, kind, i)
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


def move_photos_kind(pnum: str, category: str, from_kind: str, to_kind: str) -> dict:
    """Перемістити ВСІ фото товару з `from_kind` у `to_kind` (official↔real).
    Перейменовує файли в мірорі (`_NN`↔`_00N`), видаляє старі R2-ключі і заливає нові.
    Зберігає порядок. Повертає {moved: [..new_names..], from: [..old_names..]}."""
    if from_kind == to_kind:
        raise ValueError("from_kind == to_kind")
    if from_kind not in PHOTO_KINDS or to_kind not in PHOTO_KINDS:
        raise ValueError(f"Невідомий kind: {from_kind!r}/{to_kind!r}")
    pn = _norm(pnum)
    cat_dir = MIRROR_ROOT / category
    src = _kind_files(pn, category, from_kind)
    if not src:
        return {"moved": [], "from": []}
    start_idx = _next_index(pn, category, to_kind)
    # Фаза 1: src → тимчасові імена (щоб не зіткнутись із цільовими/одне з одним)
    tmp_map = []  # (tmp_path, target_name)
    from_names = []
    for i, old_path in enumerate(src):
        from_names.append(old_path.name)
        target = _kind_filename(pn, to_kind, start_idx + i)
        tmp = cat_dir / f"__tmp_{uuid.uuid4().hex}.webp"
        old_path.rename(tmp)
        tmp_map.append((tmp, target))
    # Фаза 2: тимчасові → фінальні + залив у R2
    moved = []
    for tmp, target in tmp_map:
        tmp.rename(cat_dir / target)
        moved.append(target)
        _sync_one(category, cat_dir / target)
    # Видалити старі R2-ключі (інші імена, тож не затремо щойно залите)
    for old in from_names:
        _delete_r2(category, old)
    return {"moved": moved, "from": from_names}


def move_one_photo(pnum: str, category: str, filename: str, to_kind: str) -> dict:
    """Перенести ОДНЕ фото в інший набір (official/real/defect). Перейменовує файл
    (наступний вільний індекс у to_kind), синкає R2, видаляє старий ключ.
    Для виправлення помилково залитих (напр. дефект потрапив у «Реальні»)."""
    if to_kind not in PHOTO_KINDS:
        raise ValueError(f"Невідомий kind: {to_kind!r}")
    pn = _norm(pnum)
    stem = Path(filename).stem
    cur_kind = next((k for k in PHOTO_KINDS if _kind_re(pn, k).match(stem)), None)
    if cur_kind is None:
        raise ValueError("Файл не належить цьому товару")
    if cur_kind == to_kind:
        return {"moved": filename, "from": filename, "unchanged": True}
    cat_dir = MIRROR_ROOT / category
    src = cat_dir / filename
    if not src.exists():
        raise ValueError(f"Файл {filename} не знайдено")
    target = _kind_filename(pn, to_kind, _next_index(pn, category, to_kind))
    tmp = cat_dir / f"__tmp_{uuid.uuid4().hex}.webp"
    src.rename(tmp)
    tmp.rename(cat_dir / target)
    _sync_one(category, cat_dir / target)
    _delete_r2(category, filename)
    return {"moved": target, "from": filename, "from_kind": cur_kind, "to_kind": to_kind}
