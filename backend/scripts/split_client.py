"""Розклеювання будь-якого клієнта на реальних власників ордерів.

Вхід: /tmp/{cid}_name_to_orders.json (з verify_client_contamination.py).

Usage:
  ./venv/bin/python3 backend/scripts/split_client.py <CID> --dry-run
  ./venv/bin/python3 backend/scripts/split_client.py <CID> --execute [--canonical "Імʼя Прізвище"]

--canonical: яке ім'я лишити НА цьому клієнті (за замовч. — first_name+last_name з БД).
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.models.models import Client
from backend.utils.name_parser import parse_client_name


def _norm_key(first: str, last: str, nickname: str) -> str:
    return "|".join([
        (first or "").strip().lower(),
        (last or "").strip().lower(),
        (nickname or "").strip().lower(),
    ])


def find_existing_client(db, source_cid: int, first: str, last: str, nickname: str) -> int | None:
    key = _norm_key(first, last, nickname)
    if key == "||":
        return None
    rows = db.execute(text("""
        SELECT DISTINCT client_id FROM client_aliases
         WHERE norm_key = :k AND client_id <> :exclude
         LIMIT 5
    """), {"k": key, "exclude": source_cid}).fetchall()
    cids = [r[0] for r in rows]
    if len(cids) == 1:
        return cids[0]
    if len(cids) > 1:
        ranked = db.execute(text("""
            SELECT c.id FROM clients c WHERE c.id = ANY(:ids)
             ORDER BY (SELECT COUNT(*) FROM orders o WHERE o.client_id=c.id) DESC, c.id ASC
             LIMIT 1
        """), {"ids": cids}).scalar()
        return ranked
    return None


def create_new_client(db, source_cid: int, first: str, last: str, nickname: str, raw_name: str) -> int:
    from datetime import datetime
    client = Client(
        first_name=first or None, last_name=last or None, nickname=nickname or None,
        created_at=datetime.utcnow(),
        notes=f"Відокремлено з #{source_cid} (mass-merge cleanup)",
    )
    db.add(client); db.flush()
    key = _norm_key(first, last, nickname)
    if key != "||":
        db.execute(text("""
            INSERT INTO client_aliases (client_id, first_name, last_name, nickname, full_raw,
                norm_key, source, seen_count, first_seen_at, last_seen_at)
            VALUES (:cid, :f, :l, :n, :raw, :k, 'split', 1, NOW(), NOW())
            ON CONFLICT (client_id, norm_key) DO NOTHING
        """), {"cid": client.id, "f": first or None, "l": last or None,
               "n": nickname or None, "raw": raw_name, "k": key})
    return client.id


def split(cid: int, execute: bool, canonical: str | None):
    mp = Path(f"/tmp/{cid}_name_to_orders.json")
    if not mp.exists():
        print(f"ERROR: {mp} not found. Run verify_client_contamination.py first.")
        sys.exit(1)
    mapping: dict[str, list[int]] = json.loads(mp.read_text())

    db = SessionLocal()
    me = db.query(Client).filter(Client.id == cid).first()
    if not me:
        print(f"ERROR: client #{cid} not found"); sys.exit(1)
    if not canonical:
        canonical = f"{me.first_name or ''} {me.last_name or ''}".strip()
    canonical_key = _norm_key(*(canonical.split() + [''])[:3])
    if not canonical_key or canonical_key == "||":
        print("ERROR: canonical name is empty — pass --canonical")
        sys.exit(1)
    print(f"Canonical owner (stays on #{cid}): «{canonical}»  key={canonical_key}")
    print(f"Mode: {'EXECUTE' if execute else 'DRY-RUN'}\n")

    stats = {"names_total": len(mapping), "kept": 0, "matched": 0, "created": 0,
             "orders_moved": 0, "orders_kept": 0}
    actions = []
    try:
        for raw_name, order_ids in sorted(mapping.items(), key=lambda x: -len(x[1])):
            p = parse_client_name(raw_name)
            first, last, nickname = (p.first_name or ""), (p.last_name or ""), (p.nickname or "")
            key = _norm_key(first, last, nickname)

            if key == canonical_key:
                stats["kept"] += 1
                stats["orders_kept"] += len(order_ids)
                actions.append(f"  KEEP «{raw_name}»: {len(order_ids)} → #{cid}")
                continue

            target_id = find_existing_client(db, cid, first, last, nickname)
            verb = "MATCH"
            if target_id is None:
                if execute:
                    target_id = create_new_client(db, cid, first, last, nickname, raw_name)
                stats["created"] += 1; verb = "CREATE"
            else:
                stats["matched"] += 1

            actions.append(f"  {verb} «{raw_name}»: {len(order_ids)} → #{target_id if target_id else '?'}")

            if execute and target_id:
                res = db.execute(text("""
                    UPDATE orders SET client_id = :t
                     WHERE id = ANY(:ids) AND client_id = :s
                """), {"t": target_id, "ids": order_ids, "s": cid})
                stats["orders_moved"] += res.rowcount or 0
                db.execute(text("""
                    INSERT INTO client_flags (client_id, flag_type, severity, peer_client_ids, details, dismissed, created_at)
                    VALUES (:cid, 'split_from', 'info', :peers, :det, TRUE, NOW())
                    ON CONFLICT DO NOTHING
                """), {"cid": target_id, "peers": [cid],
                       "det": f"Відокремлено {len(order_ids)} ордерів з #{cid} за іменем «{raw_name}»"})
        if execute:
            # Прибрати «битівні» merge-aliases на джерелі
            cleaned = db.execute(text("""
                DELETE FROM client_aliases WHERE client_id = :cid AND source = 'merge'
            """), {"cid": cid}).rowcount
            db.commit()
            print(f"✅ COMMITTED; pruned {cleaned} stale merge-aliases\n")
    except Exception as e:
        db.rollback()
        print(f"\n❌ ERROR (rolled back): {e}"); sys.exit(2)
    finally:
        db.close()

    for line in actions[:30]: print(line)
    if len(actions) > 30: print(f"  ... +{len(actions)-30} more")
    print(f"\n=== SUMMARY ===")
    for k, v in stats.items(): print(f"  {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("client_id", type=int)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    ap.add_argument("--canonical", default=None, help="ім'я, що лишається на client_id")
    args = ap.parse_args()
    split(args.client_id, args.execute, args.canonical)
