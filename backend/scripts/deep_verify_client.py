"""Глибока перевірка клієнта vs ВСІ листи ORDERS_ID.

На відміну від `verify_client_contamination.py` (який матчить лише через
`source_fingerprint`), цей скрипт індексує ВСІ рядки журналу за ключем
`(date, sorted_product_numbers)` і знаходить — який client_name стоїть у
тому ж рядку аркуша, що відповідає кожному ордеру клієнта в БД.

Це покриває:
  • легасі-ордери без `source_fingerprint`,
  • випадки коли парсер свого часу записав ордер під «не того» клієнта.

Usage: ./venv/bin/python3 backend/scripts/deep_verify_client.py <CLIENT_ID>
Output: /tmp/{cid}_deep_audit.json — повна мапа order_id → sheet_client_names
"""
from __future__ import annotations
import argparse, json, re, sys, time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.scripts.sheets_parser import get_gc, ORDERS_ID, parse_date_from_sheet_title


def _norm_pnum(p: str) -> str:
    return re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", p).upper()


def _pnum_key(pnums: list[str]) -> str:
    return "|".join(sorted(_norm_pnum(p) for p in pnums if p and p.strip()))


def load_client_orders(db, cid: int):
    rows = db.execute(text("""
        SELECT o.id, o.order_date::text AS d, o.total_amount,
               array_agg(p.productnumber ORDER BY p.productnumber) FILTER (WHERE p.productnumber IS NOT NULL) AS pnums
          FROM orders o
          LEFT JOIN order_items oi ON oi.order_id=o.id
          LEFT JOIN products p ON p.id=oi.product_id
         WHERE o.client_id=:cid
         GROUP BY o.id
    """), {"cid": cid}).fetchall()
    out = []
    for r in rows:
        pnums = r.pnums or []
        out.append({
            "id": r.id, "date": r.d, "amount": float(r.total_amount or 0),
            "pnums": pnums, "key": (r.d, _pnum_key(pnums)),
        })
    return out


def main(cid: int):
    db = SessionLocal()
    me = db.execute(text("SELECT first_name, last_name FROM clients WHERE id=:c"), {"c": cid}).first()
    if not me:
        print(f"ERROR: client #{cid} not found")
        sys.exit(1)
    canonical_name = f"{(me.first_name or '').strip()} {(me.last_name or '').strip()}".strip().lower()
    print(f"Client #{cid} canonical: «{canonical_name}»")

    orders = load_client_orders(db, cid)
    db.close()
    print(f"DB orders: {len(orders)}")

    # Index by (date, products_key) → list of order_ids
    db_index: dict[tuple, list[int]] = defaultdict(list)
    nopnum_dates: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        if o["pnums"]:
            db_index[o["key"]].append(o["id"])
        else:
            nopnum_dates[o["date"]].append(o)
    print(f"  with products: {sum(len(v) for v in db_index.values())} orders in {len(db_index)} (date,prod) groups")
    print(f"  no products:   {sum(len(v) for v in nopnum_dates.values())} orders in {len(nopnum_dates)} date-only groups")

    # Scan all sheets
    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)
    sheets = sh.worksheets()
    # Date sheets only, within client's order date range
    dates = sorted({o["date"] for o in orders})
    min_d, max_d = dates[0], dates[-1]
    print(f"Date range: {min_d} … {max_d}")

    date_sheets = []
    for ws in sheets:
        d = parse_date_from_sheet_title(ws.title)
        if not d:
            continue
        d_iso = d.isoformat()
        if min_d <= d_iso <= max_d:
            date_sheets.append((d_iso, ws))
    print(f"Will scan {len(date_sheets)} sheets in date range\n")

    order_to_sheet_names: dict[int, list[tuple[str, str, float]]] = defaultdict(list)
    nopnum_matches: dict[int, list[tuple[str, str, float]]] = defaultdict(list)
    scanned = 0
    for idx, (d_iso, ws) in enumerate(date_sheets, 1):
        try:
            rows = ws.get_all_values()
        except Exception as e:
            print(f"  ! {ws.title}: {e}")
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
        ci_sum = None
        for cand in ("Сума", "Sum", "сума"):
            if cand in header:
                ci_sum = header.index(cand)
                break
        for row in rows[1:]:
            scanned += 1
            client_name = (row[ci_client].strip() if ci_client < len(row) else "")
            pnums_raw = (row[ci_prods].strip() if ci_prods < len(row) else "")
            amt = 0.0
            if ci_sum is not None and ci_sum < len(row):
                try:
                    amt = float(re.sub(r"[^\d.]", "", (row[ci_sum] or "").replace(",", ".")))
                except Exception:
                    pass
            if pnums_raw:
                pnums = [p for p in re.split(r"[,;\n]+", pnums_raw) if p.strip()]
                key = (d_iso, _pnum_key(pnums))
                if key in db_index:
                    for oid in db_index[key]:
                        order_to_sheet_names[oid].append((ws.title, client_name, amt))
            else:
                # Без продуктів — пробуємо матчити по даті+сумі для NOPNUM-ордерів
                if d_iso in nopnum_dates and amt > 0:
                    for o in nopnum_dates[d_iso]:
                        if abs(o["amount"] - amt) < 0.01:
                            nopnum_matches[o["id"]].append((ws.title, client_name, amt))
        if idx % 20 == 0:
            matched_so_far = len(order_to_sheet_names) + len(nopnum_matches)
            print(f"  [{idx}/{len(date_sheets)}] scanned {scanned} rows, matched {matched_so_far} orders so far")
        time.sleep(0.4)

    # Analysis
    own = 0
    stranger = 0
    by_stranger_name: dict[str, list[int]] = defaultdict(list)
    unmatched = 0
    ambiguous = 0

    for o in orders:
        matches = order_to_sheet_names.get(o["id"]) or nopnum_matches.get(o["id"]) or []
        if not matches:
            unmatched += 1
            continue
        names = {m[1].strip().lower() for m in matches if m[1].strip()}
        if not names:
            unmatched += 1
            continue
        # «Світлана Лана» вважаємо своєю (з варіантами невидимих пробілів)
        own_names = {n for n in names if canonical_name in re.sub(r"\s+", " ", n)}
        non_own = names - own_names
        if non_own and not own_names:
            stranger += 1
            for nm in non_own:
                by_stranger_name[nm].append(o["id"])
        elif non_own and own_names:
            ambiguous += 1
            for nm in non_own:
                by_stranger_name[f"{nm} ⚠️AMBIG"].append(o["id"])
        else:
            own += 1

    print(f"\n=== РЕЗУЛЬТАТ для #{cid} ({canonical_name}) ===")
    print(f"  всього у БД:        {len(orders)}")
    print(f"  ✓ своїх:            {own}")
    print(f"  ✗ чужих:            {stranger}")
    print(f"  ⚠ неоднозначних:    {ambiguous}  (рядок зі своїм + чужим іменем)")
    print(f"  ? без матчу в листі: {unmatched}")
    if by_stranger_name:
        print(f"\n  Топ чужих імен:")
        for nm, ids in sorted(by_stranger_name.items(), key=lambda x: -len(x[1]))[:25]:
            print(f"    {len(ids):4d} ордерів → «{nm}»")

    # Persist
    out = {
        "canonical": canonical_name,
        "summary": {"total": len(orders), "own": own, "stranger": stranger,
                    "ambiguous": ambiguous, "unmatched": unmatched},
        "stranger_map": {nm: ids for nm, ids in by_stranger_name.items()},
        "order_to_names": {str(oid): [{"sheet": m[0], "name": m[1], "amount": m[2]}
                                      for m in (order_to_sheet_names.get(oid) or nopnum_matches.get(oid) or [])]
                          for oid in {o["id"] for o in orders}},
    }
    p = Path(f"/tmp/{cid}_deep_audit.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nFull mapping → {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("client_id", type=int)
    args = ap.parse_args()
    main(args.client_id)
