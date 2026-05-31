"""Розклеювання клієнта #41855 (Елена Русак) на реальних власників ордерів.

Вхід: /tmp/41855_name_to_orders.json (генерується verify_41855_contamination.py).
Для кожного name_from_sheet:
  1. Спроба знайти існуючого клієнта через name_parser+alias-lookup (як парсер).
     Виключаємо самого #41855 з кандидатів.
  2. Якщо знайдено → переносимо ордери до нього.
  3. Якщо НЕ знайдено → створюємо нового, копіюємо alias, реєструємо first/last/nickname.
  4. Аудит-флаг 'split_from' на новому/цільовому клієнті з peer=[41855].

#41855 в кінці лишає лише ордери з імені «Елена Русак» + 36 легасі.
Транзакційно, з dry-run прапором.

Запуск:  ./venv/bin/python3 backend/scripts/split_41855.py --dry-run
         ./venv/bin/python3 backend/scripts/split_41855.py --execute
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text
from backend.models.database import SessionLocal
from backend.models.models import Client
from backend.utils.name_parser import parse_client_name

SOURCE_CLIENT_ID = 41855
SOURCE_CANONICAL = "елена русак"  # імʼя самої власниці, ордери з ним лишаються
MAPPING_PATH = Path("/tmp/41855_name_to_orders.json")


def _norm_key(first: str, last: str, nickname: str) -> str:
    return "|".join([
        (first or "").strip().lower(),
        (last or "").strip().lower(),
        (nickname or "").strip().lower(),
    ])


def find_existing_client(db, first: str, last: str, nickname: str) -> int | None:
    """Match by alias norm_key (як парсер). Виключаємо source client."""
    key = _norm_key(first, last, nickname)
    if key == "||":
        return None
    rows = db.execute(text("""
        SELECT DISTINCT client_id FROM client_aliases
         WHERE norm_key = :k AND client_id <> :exclude
         LIMIT 5
    """), {"k": key, "exclude": SOURCE_CLIENT_ID}).fetchall()
    cids = [r[0] for r in rows]
    if len(cids) == 1:
        return cids[0]
    if len(cids) > 1:
        # ambiguity: оберемо з найбільшою кількістю замовлень
        ranked = db.execute(text("""
            SELECT c.id FROM clients c
             WHERE c.id = ANY(:ids)
             ORDER BY (SELECT COUNT(*) FROM orders o WHERE o.client_id=c.id) DESC, c.id ASC
             LIMIT 1
        """), {"ids": cids}).scalar()
        return ranked
    return None


def create_new_client(db, first: str, last: str, nickname: str, raw_name: str) -> int:
    from datetime import datetime
    client = Client(
        first_name=first or None,
        last_name=last or None,
        nickname=nickname or None,
        created_at=datetime.utcnow(),
        notes=f"Відокремлено з #{SOURCE_CLIENT_ID} (mass-merge cleanup 25.05.2026)",
    )
    db.add(client)
    db.flush()

    key = _norm_key(first, last, nickname)
    if key != "||":
        db.execute(text("""
            INSERT INTO client_aliases
                (client_id, first_name, last_name, nickname, full_raw,
                 norm_key, source, seen_count, first_seen_at, last_seen_at)
            VALUES (:cid, :f, :l, :n, :raw, :k, 'split', 1, NOW(), NOW())
            ON CONFLICT (client_id, norm_key) DO NOTHING
        """), {"cid": client.id, "f": first or None, "l": last or None,
               "n": nickname or None, "raw": raw_name, "k": key})

    return client.id


def split(execute: bool):
    if not MAPPING_PATH.exists():
        print(f"ERROR: {MAPPING_PATH} not found. Run verify_41855_contamination.py first.")
        sys.exit(1)
    mapping: dict[str, list[int]] = json.loads(MAPPING_PATH.read_text())
    print(f"Loaded {len(mapping)} sheet-names, sum={sum(len(v) for v in mapping.values())} orders")
    print(f"Mode: {'EXECUTE (writes DB)' if execute else 'DRY-RUN (no writes)'}\n")

    db = SessionLocal()
    stats = {
        "names_total": len(mapping),
        "names_kept": 0,    # «Елена Русак» — лишаємо на #41855
        "names_matched": 0,  # знайдено існуючого
        "names_created": 0,  # створено нового
        "orders_moved": 0,
        "orders_kept": 0,
        "errors": [],
    }
    actions = []

    try:
        for raw_name, order_ids in sorted(mapping.items(), key=lambda x: -len(x[1])):
            parsed = parse_client_name(raw_name)
            first = parsed.first_name or ""
            last = parsed.last_name or ""
            nickname = parsed.nickname or ""
            key = _norm_key(first, last, nickname)

            # Власниця залишається на #41855
            if key.startswith(SOURCE_CANONICAL.replace(' ', '|') + '|') or key.split('|')[:2] == SOURCE_CANONICAL.split():
                stats["names_kept"] += 1
                stats["orders_kept"] += len(order_ids)
                actions.append((f"  KEEP «{raw_name}»: {len(order_ids)} orders → #{SOURCE_CLIENT_ID}", None))
                continue

            target_id = find_existing_client(db, first, last, nickname)
            verb = "MATCH"
            if target_id is None:
                if execute:
                    target_id = create_new_client(db, first, last, nickname, raw_name)
                stats["names_created"] += 1
                verb = "CREATE"
            else:
                stats["names_matched"] += 1

            actions.append((f"  {verb} «{raw_name}»: {len(order_ids)} orders → "
                           f"#{target_id if target_id else '?'}", order_ids if execute else None))

            if execute and target_id:
                # Перенос ордерів
                res = db.execute(text("""
                    UPDATE orders SET client_id = :t
                     WHERE id = ANY(:ids) AND client_id = :s
                """), {"t": target_id, "ids": order_ids, "s": SOURCE_CLIENT_ID})
                stats["orders_moved"] += res.rowcount or 0
                # Аудит-флаг
                db.execute(text("""
                    INSERT INTO client_flags
                        (client_id, flag_type, severity, peer_client_ids, details, dismissed, created_at)
                    VALUES (:cid, 'split_from', 'info', :peers, :det, TRUE, NOW())
                    ON CONFLICT DO NOTHING
                """), {"cid": target_id, "peers": [SOURCE_CLIENT_ID],
                       "det": f"Відокремлено {len(order_ids)} ордерів з контамінованого #{SOURCE_CLIENT_ID} за реальним імʼям з аркуша «{raw_name}»"})
        if execute:
            db.commit()
            print("✅ COMMITTED\n")
    except Exception as e:
        db.rollback()
        print(f"\n❌ ERROR (rolled back): {e}")
        stats["errors"].append(str(e))
        sys.exit(2)
    finally:
        db.close()

    # Друк
    for line, _ in actions[:30]:
        print(line)
    if len(actions) > 30:
        print(f"  ... +{len(actions)-30} more")
    print(f"\n=== SUMMARY ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    split(execute=args.execute)
