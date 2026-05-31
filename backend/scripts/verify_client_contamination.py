"""READ-ONLY перевірка контамінації для будь-якого client_id.

Сканує Sheets «Замовлення» (ORDERS_ID), відтворює `source_fingerprint` для
кожного рядка тією ж формулою що й парсер ([sheets_parser.py:2887](backend/scripts/sheets_parser.py:2887))
і повертає мапу {реальне_імʼя_з_аркуша → [order_ids]} для заданого клієнта.

Usage: ./venv/bin/python3 backend/scripts/verify_client_contamination.py <CLIENT_ID>
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys, time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.scripts.sheets_parser import get_gc, ORDERS_ID, parse_date_from_sheet_title


def _norm_pnum(p: str) -> str:
    return re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", p).upper()


def compute_fp(client_name: str, order_date_iso: str, product_nums: list[str]) -> str:
    norm = sorted(_norm_pnum(p) for p in product_nums if p.strip())
    raw = f"{(client_name or '').strip().lower()}|{order_date_iso}|{'|'.join(norm)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def load_target_orders(db, cid: int):
    rows = db.execute(text("""
        SELECT o.id, o.order_date::text AS d, o.source_fingerprint,
               array_agg(p.productnumber ORDER BY p.productnumber) FILTER (WHERE p.productnumber IS NOT NULL) AS pnums
          FROM orders o
          LEFT JOIN order_items oi ON oi.order_id=o.id
          LEFT JOIN products p ON p.id=oi.product_id
         WHERE o.client_id=:cid
         GROUP BY o.id
    """), {"cid": cid}).fetchall()
    out, null_fp = {}, []
    for r in rows:
        if r.source_fingerprint:
            out[r.source_fingerprint] = (r.id, r.d, r.pnums or [])
        else:
            null_fp.append((r.id, r.d, r.pnums or []))
    return out, null_fp


def main(cid: int):
    db = SessionLocal()
    fp_to_order, null_fp_orders = load_target_orders(db, cid)
    db.close()
    print(f"Client #{cid}: {len(fp_to_order)} orders with fp + {len(null_fp_orders)} legacy NULL-fp")

    needed_dates = {info[1] for info in fp_to_order.values()}
    print(f"Distinct order dates: {len(needed_dates)}")

    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)
    sheets = sh.worksheets()
    print(f"Opened ORDERS journal — {len(sheets)} sheets")

    date_to_sheets = defaultdict(list)
    for ws in sheets:
        d = parse_date_from_sheet_title(ws.title)
        if d:
            date_to_sheets[d.isoformat()].append(ws)
    sheets_to_scan = []
    for d in sorted(needed_dates):
        sheets_to_scan.extend(date_to_sheets.get(d, []))
    print(f"Will scan {len(sheets_to_scan)} matching sheets\n")

    found = defaultdict(list)
    unmatched_fps = set(fp_to_order.keys())
    scanned_rows = 0
    for idx, ws in enumerate(sheets_to_scan, 1):
        try:
            rows = ws.get_all_values()
        except Exception as e:
            print(f"  ! skipped {ws.title}: {e}")
            time.sleep(5)
            continue
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]
        try:
            ci_client = header.index("Клієнт")
            ci_prods = header.index("Номера товарів")
        except ValueError:
            continue
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
            pnums = [p for p in re.split(r"[,;\n]+", pnums_raw) if p.strip()]
            fp = compute_fp(client_name, d_iso, pnums)
            if fp in fp_to_order:
                order_id, _, _ = fp_to_order[fp]
                found[client_name].append(order_id)
                unmatched_fps.discard(fp)
        if idx % 10 == 0:
            print(f"  [{idx}/{len(sheets_to_scan)}] scanned, found {sum(len(v) for v in found.values())} so far")
        time.sleep(0.4)

    out_path = Path(f"/tmp/{cid}_name_to_orders.json")
    out_path.write_text(json.dumps({k: sorted(set(v)) for k, v in found.items()}, ensure_ascii=False, indent=2))
    print(f"\nMapping saved → {out_path}")
    print(f"\n=== {len(found)} різних імен породили ордери #{cid} ===\n")
    for name, ids in sorted(found.items(), key=lambda x: -len(x[1])):
        print(f"  {len(ids):4d} ордерів  →  «{name}»")
    print(f"\n  {len(unmatched_fps):4d} fingerprint-ів не зматчилися")
    print(f"  {len(null_fp_orders):4d} legacy NULL-fp orders")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("client_id", type=int)
    args = ap.parse_args()
    main(args.client_id)
