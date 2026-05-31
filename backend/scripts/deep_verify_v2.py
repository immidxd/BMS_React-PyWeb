"""Глибока перевірка v2: всі рядки усіх листів індексуємо ОДИН РАЗ за
двома ключами: (date, products_key) та (date, amount_int). Для кожного
ордера клієнта пробуємо обидва шляхи, мажоритарне голосування за іменем.

Покриває:
  • легасі-ордери без source_fingerprint,
  • ордери де productnumber дрифтував (інакше в листі ніж у БД),
  • ордери без позицій у БД (NULL-pnum).

Output: /tmp/{cid}_v2_audit.json з полями stranger_map / unmatched / own.
"""
from __future__ import annotations
import argparse, json, re, sys, time
from collections import defaultdict, Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.scripts.sheets_parser import get_gc, ORDERS_ID, parse_date_from_sheet_title


def _np(p: str) -> str:
    return re.sub(r"[^\wА-ЯҐЄІЇа-яґєії]", "", p).upper()


def pk(pnums):
    return "|".join(sorted(_np(p) for p in pnums if p and p.strip()))


def amt_key(amount: float) -> int:
    return int(round(amount))


def load_orders(db, cid: int):
    rows = db.execute(text("""
        SELECT o.id, o.order_date::text AS d, COALESCE(o.total_amount, 0)::float AS amt,
               array_agg(p.productnumber ORDER BY p.productnumber) FILTER (WHERE p.productnumber IS NOT NULL) AS pnums
          FROM orders o
          LEFT JOIN order_items oi ON oi.order_id=o.id
          LEFT JOIN products p ON p.id=oi.product_id
         WHERE o.client_id=:cid
         GROUP BY o.id
    """), {"cid": cid}).fetchall()
    return [{"id": r.id, "d": r.d, "amt": r.amt, "pnums": r.pnums or []} for r in rows]


def main(cid: int):
    db = SessionLocal()
    me = db.execute(text("SELECT first_name, last_name FROM clients WHERE id=:c"), {"c": cid}).first()
    canonical = f"{(me.first_name or '').strip()} {(me.last_name or '').strip()}".strip().lower()
    print(f"Client #{cid} canonical: «{canonical}»")
    orders = load_orders(db, cid)
    db.close()
    print(f"Orders to verify: {len(orders)}")

    dates = sorted({o["d"] for o in orders})
    min_d, max_d = dates[0], dates[-1]

    gc = get_gc()
    sh = gc.open_by_key(ORDERS_ID)
    sheets = sh.worksheets()
    todo = [(parse_date_from_sheet_title(ws.title).isoformat(), ws)
            for ws in sheets
            if parse_date_from_sheet_title(ws.title)
            and min_d <= parse_date_from_sheet_title(ws.title).isoformat() <= max_d]
    print(f"Sheets to index: {len(todo)}\n")

    # Глобальні індекси
    by_dp: dict[tuple, list[str]] = defaultdict(list)   # (date, pkey) → [client_name,...]
    by_da: dict[tuple, list[str]] = defaultdict(list)   # (date, amt_int) → [client_name,...]
    by_d:  dict[str,   list[tuple]] = defaultdict(list) # date → [(name, pkey, amt),...]
    scanned = 0
    for idx, (d_iso, ws) in enumerate(todo, 1):
        try:
            rows = ws.get_all_values()
        except Exception as e:
            print(f"  ! {ws.title}: {e}"); time.sleep(5); continue
        if not rows: continue
        header = [h.strip() for h in rows[0]]
        try:
            ci_c = header.index("Клієнт"); ci_p = header.index("Номера товарів")
        except ValueError:
            continue
        ci_s = None
        for cand in ("Сума", "Sum", "сума"):
            if cand in header:
                ci_s = header.index(cand); break
        for row in rows[1:]:
            scanned += 1
            name = (row[ci_c].strip() if ci_c < len(row) else "")
            praw = (row[ci_p].strip() if ci_p < len(row) else "")
            amt = 0.0
            if ci_s is not None and ci_s < len(row):
                try:
                    amt = float(re.sub(r"[^\d.]", "", (row[ci_s] or "").replace(",", ".")))
                except Exception:
                    pass
            if not name and not praw:
                continue
            pnums = [p for p in re.split(r"[,;\n]+", praw) if p.strip()]
            pkey = pk(pnums)
            ak = amt_key(amt)
            by_d[d_iso].append((name, pkey, amt))
            if pkey:
                by_dp[(d_iso, pkey)].append(name)
            if ak > 0:
                by_da[(d_iso, ak)].append(name)
        if idx % 20 == 0:
            print(f"  [{idx}/{len(todo)}] scanned {scanned} rows")
        time.sleep(0.35)
    print(f"\nTotal sheet rows indexed: {scanned}\n")

    # Резолв для кожного ордера: pkey-match > date+amount-match > unmatched
    own = stranger = ambig = unmatched = 0
    by_stranger = defaultdict(list)
    order_winner = {}
    for o in orders:
        cands: list[str] = []
        used = None
        if o["pnums"]:
            key = (o["d"], pk(o["pnums"]))
            if key in by_dp:
                cands = list(by_dp[key]); used = "products"
        if not cands and o["amt"] > 0:
            ak = (o["d"], amt_key(o["amt"]))
            if ak in by_da:
                cands = list(by_da[ak]); used = "amount"
        if not cands:
            unmatched += 1
            continue
        # Голосування
        norm = [re.sub(r"\s+", " ", c.strip()).lower() for c in cands if c.strip()]
        if not norm:
            unmatched += 1; continue
        cnt = Counter(norm)
        winner = cnt.most_common(1)[0][0]
        if canonical in winner:
            own += 1
            order_winner[o["id"]] = ("own", winner, used)
        else:
            # перевір чи присутнє своє ім'я серед кандидатів
            has_own = any(canonical in n for n in cnt)
            if has_own:
                ambig += 1
                # Якщо в одному рядку було і своє і чуже — це carry-over. Не чіпаємо.
                order_winner[o["id"]] = ("ambig", winner, used)
            else:
                stranger += 1
                pretty = ' '.join(w.capitalize() for w in winner.split())
                by_stranger[pretty].append(o["id"])
                order_winner[o["id"]] = ("stranger", pretty, used)

    print(f"=== РЕЗУЛЬТАТ для #{cid} ({canonical}) ===")
    print(f"  всього у БД:        {len(orders)}")
    print(f"  ✓ своїх:            {own}")
    print(f"  ✗ чужих:            {stranger}")
    print(f"  ⚠ неоднозначних:    {ambig}")
    print(f"  ? без матчу:        {unmatched}")
    if by_stranger:
        print(f"\n  Топ чужих (winners):")
        for nm, ids in sorted(by_stranger.items(), key=lambda x: -len(x[1]))[:25]:
            print(f"    {len(ids):4d} → «{nm}»")

    out = {
        "canonical": canonical,
        "summary": {"total": len(orders), "own": own, "stranger": stranger,
                    "ambiguous": ambig, "unmatched": unmatched},
        "stranger_map": {k: sorted(v) for k, v in by_stranger.items()},
        "order_decisions": {str(k): {"kind": v[0], "winner": v[1], "matched_by": v[2]}
                            for k, v in order_winner.items()},
    }
    p = Path(f"/tmp/{cid}_v2_audit.json")
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nSaved → {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("client_id", type=int)
    args = ap.parse_args()
    main(args.client_id)
