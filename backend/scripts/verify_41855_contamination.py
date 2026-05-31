"""READ-ONLY перевірка: для клієнта #41855 (Елена Русак / nick «Ирина Ирина»)
повертає словник {real_client_name_from_sheet: [order_ids...]} шляхом
відтворення `source_fingerprint` за тією самою формулою, що й парсер
([sheets_parser.py:2887](backend/scripts/sheets_parser.py:2887)).

Нічого не модифікує в БД. Лише читає Google Sheets і друкує звіт.
"""
from __future__ import annotations
import hashlib, re, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.scripts.sheets_parser import get_gc, ORDERS_ID, parse_date_from_sheet_title

TARGET_CLIENT_ID = 41855


def _norm_pnum(p: str) -> str:
    return re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", p).upper()


def compute_fp(client_name: str, order_date_iso: str, product_nums: list[str]) -> str:
    norm = sorted(_norm_pnum(p) for p in product_nums if p.strip())
    raw = f"{(client_name or '').strip().lower()}|{order_date_iso}|{'|'.join(norm)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def load_target_orders(db) -> dict:
    """Returns {fingerprint: (order_id, date_iso, product_nums)} for target client."""
    rows = db.execute(text("""
        SELECT o.id, o.order_date::text AS d, o.source_fingerprint,
               array_agg(p.productnumber ORDER BY p.productnumber) FILTER (WHERE p.productnumber IS NOT NULL) AS pnums
          FROM orders o
          LEFT JOIN order_items oi ON oi.order_id=o.id
          LEFT JOIN products p ON p.id=oi.product_id
         WHERE o.client_id=:cid
         GROUP BY o.id
    """), {"cid": TARGET_CLIENT_ID}).fetchall()
    out: dict[str, tuple] = {}
    null_fp = []
    for r in rows:
        if r.source_fingerprint:
            out[r.source_fingerprint] = (r.id, r.d, r.pnums or [])
        else:
            null_fp.append((r.id, r.d, r.pnums or []))
    return out, null_fp


def main():
    db = SessionLocal()
    fp_to_order, null_fp_orders = load_target_orders(db)
    db.close()
    print(f"Target client #{TARGET_CLIENT_ID}: {len(fp_to_order)} orders with fingerprint + {len(null_fp_orders)} legacy NULL-fp")

    # Дати, які треба пошукати
    needed_dates = {info[1] for info in fp_to_order.values()}  # YYYY-MM-DD
    print(f"Need to scan sheets for {len(needed_dates)} distinct dates")

    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)
    sheets = sh.worksheets()
    print(f"Opened journal — {len(sheets)} total sheets")

    # Map sheet → date
    date_to_sheets: dict[str, list] = defaultdict(list)
    for ws in sheets:
        d = parse_date_from_sheet_title(ws.title)
        if d:
            date_to_sheets[d.isoformat()].append(ws)

    sheets_to_scan = []
    for d in sorted(needed_dates):
        sheets_to_scan.extend(date_to_sheets.get(d, []))
    print(f"Will scan {len(sheets_to_scan)} matching sheets\n")

    # Скан
    found: dict[str, list[int]] = defaultdict(list)  # client_name → [order_ids]
    unmatched_fps = set(fp_to_order.keys())
    scanned_rows = 0

    import time
    for idx, ws in enumerate(sheets_to_scan, 1):
        try:
            rows = ws.get_all_values()
        except Exception as e:
            print(f"  ! skipped {ws.title}: {e}")
            time.sleep(5)
            continue
        if not rows:
            print(f"  [{idx}/{len(sheets_to_scan)}] {ws.title}: EMPTY")
            continue
        header = [h.strip() for h in rows[0]]
        try:
            ci_client = header.index("Клієнт")
            ci_prods = header.index("Номера товарів")
        except ValueError:
            print(f"  [{idx}/{len(sheets_to_scan)}] {ws.title}: no expected headers, got {header[:6]}")
            continue
        print(f"  [{idx}/{len(sheets_to_scan)}] {ws.title}: {len(rows)-1} data rows")
        time.sleep(0.5)  # gentle rate-limit
        sheet_date = parse_date_from_sheet_title(ws.title)
        if not sheet_date:
            continue
        d_iso = sheet_date.isoformat()
        for row in rows[1:]:
            scanned_rows += 1
            client_name = row[ci_client].strip() if ci_client < len(row) else ""
            pnums_raw = row[ci_prods].strip() if ci_prods < len(row) else ""
            if not pnums_raw:
                continue
            # Парсер сплітить по комах/перенесеннях. Тут спрощено — те ж саме.
            pnums = [p for p in re.split(r"[,;\n]+", pnums_raw) if p.strip()]
            fp = compute_fp(client_name, d_iso, pnums)
            if fp in fp_to_order:
                order_id, _, _ = fp_to_order[fp]
                found[client_name].append(order_id)
                unmatched_fps.discard(fp)

    print(f"Scanned {scanned_rows} rows across {len(sheets_to_scan)} sheets")
    # Persist mapping for the split script
    import json
    out_path = Path("/tmp/41855_name_to_orders.json")
    out_path.write_text(json.dumps({k: sorted(set(v)) for k, v in found.items()}, ensure_ascii=False, indent=2))
    print(f"Mapping saved → {out_path}")

    print(f"\n=== РЕЗУЛЬТАТ: {len(found)} різних імен у sheets-рядках, що породили ордери #{TARGET_CLIENT_ID} ===\n")
    for name, ids in sorted(found.items(), key=lambda x: -len(x[1])):
        print(f"  {len(ids):4d} ордерів  →  «{name}»")
    print(f"\n  {len(unmatched_fps):4d} fingerprint-ів не зматчилися (sheet міг бути видалений/перейменований)")
    print(f"  {len(null_fp_orders):4d} ордерів без fingerprint взагалі (легасі)")


if __name__ == "__main__":
    main()
