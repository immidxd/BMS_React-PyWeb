"""Номери товарів, що дублюються, — для червоної позначки в інтерфейсі.

Номер у BMS НЕ унікальний (див. пам'ять «product-number-duplicates»), і частина
повторів законна. Тому «дубль» тут — лише те, що людині варто перевірити:

  • номер мають товари з РІЗНИХ завозів (переважно це повторно використаний
    номер: #Ф1971 — і в 01.05.2025, і в 09.05.2025, з різними брендами);
  • у межах ОДНОГО завозу записи з цим номером суперечать: різний бренд або
    різний вид (обидва заповнені) — #А1124 «Шльопанці EA7 / Кросівки Sprandi».

НЕ дубль:
  • ростовка — той самий номер, різні розміри в одному завозі;
  • суфіксні варіанти (#В37-2) — інший номер, їх веде сам журнал;
  • службові «???» і тимчасові __tmp_rename_*;
  • старі номери без «#» (4401 vs #4401) — свідомо лишені як є, це
    історичний імпорт 2025-06-01, а не помилка, яку треба лагодити.

Читає лише локальну базу (bsstorage), хмару не чіпає.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, Optional, Tuple

from sqlalchemy import text

Row = Tuple[str, Optional[int], Optional[str], Optional[int], Optional[int]]


def _skip(number: str) -> bool:
    n = (number or "").strip()
    return not n or "???" in n or n.startswith("__tmp_rename")


def find_duplicates(rows: Iterable[Row]) -> Dict[str, Dict[str, Any]]:
    """rows: (productnumber, deliveryid, deliveryname, brandid, typeid)."""
    groups: Dict[str, list] = defaultdict(list)
    for pn, did, dname, brand, typ in rows:
        if not _skip(pn):
            groups[pn].append((did, dname, brand, typ))

    out: Dict[str, Dict[str, Any]] = {}
    for pn, recs in groups.items():
        if len(recs) < 2:
            continue
        deliveries = {did: dname for did, dname, _, _ in recs if did is not None}
        reason = None
        if len(deliveries) > 1:
            reason = "deliveries"
        else:
            by_delivery: Dict[Optional[int], list] = defaultdict(list)
            for did, _, brand, typ in recs:
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
        SELECT p.productnumber, p.deliveryid, d.deliveryname, p.brandid, p.typeid
        FROM products p
        LEFT JOIN deliveries d ON d.id = p.deliveryid
        WHERE p.productnumber IN (
            SELECT productnumber FROM products
            WHERE productnumber IS NOT NULL
            GROUP BY productnumber HAVING COUNT(*) > 1
        )
    """)).fetchall()
    return find_duplicates(rows)
