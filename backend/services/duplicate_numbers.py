"""Номери товарів, що дублюються, — для червоної позначки в інтерфейсі.

Номер у BMS НЕ унікальний (див. пам'ять «product-number-duplicates»), і частина
повторів законна. Тому «дубль» тут — лише те, що людині варто перевірити:

  • номер мають товари з РІЗНИХ завозів (переважно це повторно використаний
    номер: #Ф1971 — і в 01.05.2025, і в 09.05.2025, з різними брендами);
  • у межах ОДНОГО завозу записи з цим номером суперечать: різний бренд або
    різний вид (обидва заповнені) — #А1124 «Шльопанці EA7 / Кросівки Sprandi».

НЕ дубль:
  • ростовка — той самий номер, різні розміри того самого товару (бренд, вид,
    модель), навіть із РІЗНИХ завозів: розміри часто докуповують окремо
    (#Ф4350: 36–38 і 40 — завіз 20.08, 39 — 13.08). Рішення власника 17.10.2026:
    червоний — лише справжній дубль, ростовка — ніколи. Повторений чи порожній
    розмір — уже не чиста ростовка, тож різні завози тоді лишаються червоними;
  • суфіксні варіанти (#В37-2) — інший номер, їх веде сам журнал;
  • службові «???» і тимчасові __tmp_rename_*;
  • старі номери без «#» (4401 vs #4401) — свідомо лишені як є, це
    історичний імпорт 2025-06-01, а не помилка, яку треба лагодити.

Читає лише локальну базу (bsstorage), хмару не чіпає.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy import text

try:
    from services.size_normalization import decimalize_fractions
except ImportError:
    from backend.services.size_normalization import decimalize_fractions

# (productnumber, deliveryid, deliveryname, brandid, typeid[, model, size])
Row = Tuple[Any, ...]


def _skip(number: str) -> bool:
    n = (number or "").strip()
    return not n or "???" in n or n.startswith("__tmp_rename")


def _size_key(size) -> str:
    """Розмір для порівняння, як sheets_parser._size_canon: '38⅔' = '38.6',
    '44,6' = '44.6', '42.0' = '42'. Порожній — ''."""
    s = str(size or "").strip().replace(",", ".")
    if not s:
        return ""
    s = re.sub(r"\s+", "", str(decimalize_fractions(s)))
    if re.fullmatch(r"\d+/\d+", s):
        s = s.replace("/", "-")
    if re.fullmatch(r"\d+\.\d+", s):
        s = s.rstrip("0").rstrip(".")
    return s.lower()


def _is_size_run(recs: list) -> bool:
    """Ростовка: той самий товар (серед заповнених бренд, вид, модель — по одному) і
    в КОЖНОГО запису свій неповторний розмір."""
    sizes = [_size_key(r[5]) for r in recs]
    return (all(sizes) and len(set(sizes)) == len(sizes)
            and all(len({r[i] for r in recs if r[i]}) <= 1 for i in (2, 3, 4)))


def find_duplicates(rows: Iterable[Row]) -> Dict[str, Dict[str, Any]]:
    """rows: (productnumber, deliveryid, deliveryname, brandid, typeid[, model, size])."""
    groups: Dict[str, list] = defaultdict(list)
    for pn, did, dname, brand, typ, *rest in rows:
        model, size = (list(rest) + [None, None])[:2]
        if not _skip(pn):
            groups[pn].append((did, dname, brand, typ, (model or "").strip().lower(), size))

    out: Dict[str, Dict[str, Any]] = {}
    for pn, recs in groups.items():
        if len(recs) < 2:
            continue
        deliveries = {r[0]: r[1] for r in recs if r[0] is not None}
        reason = None
        if len(deliveries) > 1 and not _is_size_run(recs):
            reason = "deliveries"
        else:
            by_delivery: Dict[Optional[int], list] = defaultdict(list)
            for did, _, brand, typ, _, _ in recs:
                by_delivery[did].append((brand, typ))
            for items in by_delivery.values():
                brands = {b for b, _ in items if b}
                types = {t for _, t in items if t}
                if len(brands) > 1 or len(types) > 1:
                    reason = "conflict"
                    break
        if reason:
            out[pn] = {
                "records": len(recs),
                "deliveries": sorted(n for n in deliveries.values() if n)[:6],
                "reason": reason,
            }
    return out


def load_duplicates(db) -> Dict[str, Dict[str, Any]]:
    rows = db.execute(text("""
        SELECT p.productnumber, p.deliveryid, d.deliveryname, p.brandid, p.typeid, p.model,
               COALESCE(NULLIF(BTRIM(p.sizeeu), ''), NULLIF(BTRIM(p.size_letter), ''),
                        NULLIF(BTRIM(p.measurementscm::text), '')) AS size
        FROM products p
        LEFT JOIN deliveries d ON d.id = p.deliveryid
        WHERE p.productnumber IN (
            SELECT productnumber FROM products
            WHERE productnumber IS NOT NULL
            GROUP BY productnumber HAVING COUNT(*) > 1
        )
    """)).fetchall()
    return find_duplicates(rows)
