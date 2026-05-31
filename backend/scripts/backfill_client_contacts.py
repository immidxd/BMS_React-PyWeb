"""Backfill client_contacts з існуючих clients.* primary-колонок.

Стратегія:
  * Для кожного клієнта прокидаємо всі непорожні канали (phone/fb/tg/ig/email/
    viber/olx/tiktok/messenger) як рядки в client_contacts з is_primary=TRUE,
    source='backfill'.
  * UNIQUE(kind, normalized) природно ловить колізії (двоє клієнтів мають
    однаковий нормалізований телефон / FB / TG / IG) — такі рядки скіпаємо
    і логуємо в окрему таблицю collisions для подальшого мерджу.

Idempotent: ON CONFLICT DO NOTHING. Можна перезапускати.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from backend.models.database import SessionLocal  # noqa: E402
from backend.utils.identity_normalizer import (  # noqa: E402
    normalize_phone,
    normalize_facebook,
    normalize_telegram,
    normalize_instagram,
    normalize_email,
)


# kind, source_column, normalizer (None → store raw lowercased as normalized)
_CHANNELS = [
    ("phone",     "phone_number", normalize_phone),
    ("facebook",  "facebook",     normalize_facebook),
    ("telegram",  "telegram",     normalize_telegram),
    ("instagram", "instagram",    normalize_instagram),
    ("email",     "email",        normalize_email),
    ("viber",     "viber",        normalize_phone),  # viber == phone
    ("olx",       "olx",          None),
    ("tiktok",    "tiktok",       None),
    ("messenger", "messenger",    None),
]


_INVALID = {"#N/A", "#REF!", "#VALUE!", "#ERROR!", "#NAME?", "#NULL!", "#DIV/0!", "#NUM!"}


def _norm_fallback(v: str) -> str:
    return (v or "").strip().lower()


def _is_garbage(v: str) -> bool:
    s = (v or "").strip().upper()
    return not s or s in _INVALID or s == "ㅤ"


def backfill() -> dict:
    db = SessionLocal()
    inserted = 0
    skipped_empty = 0
    skipped_conflict = 0
    collisions: list[tuple[str, str, int, int]] = []

    try:
        rows = db.execute(text("""
            SELECT id, phone_number, facebook, telegram, instagram, email,
                   viber, olx, tiktok, messenger
              FROM clients
             ORDER BY id
        """)).mappings().all()

        for r in rows:
            cid = r["id"]
            for kind, col, normalizer in _CHANNELS:
                raw = (r[col] or "").strip()
                if _is_garbage(raw):
                    skipped_empty += 1
                    continue
                if normalizer is not None:
                    norm = normalizer(raw) or _norm_fallback(raw)
                else:
                    norm = _norm_fallback(raw)
                if not norm:
                    skipped_empty += 1
                    continue

                # Чи вже зайнятий цей normalized?
                owner = db.execute(text("""
                    SELECT client_id FROM client_contacts
                     WHERE kind = :k AND normalized = :n
                     LIMIT 1
                """), {"k": kind, "n": norm}).scalar()
                if owner is not None and owner != cid:
                    collisions.append((kind, norm, owner, cid))
                    skipped_conflict += 1
                    continue

                res = db.execute(text("""
                    INSERT INTO client_contacts
                        (client_id, kind, value, normalized, is_primary, source)
                    VALUES
                        (:cid, :k, :v, :n, TRUE, 'backfill')
                    ON CONFLICT DO NOTHING
                """), {"cid": cid, "k": kind, "v": raw, "n": norm})
                if res.rowcount:
                    inserted += 1

            if cid % 500 == 0:
                db.commit()

        db.commit()
    finally:
        db.close()

    return {
        "inserted": inserted,
        "skipped_empty": skipped_empty,
        "skipped_conflict": skipped_conflict,
        "collisions_sample": collisions[:30],
        "total_collisions": len(collisions),
    }


if __name__ == "__main__":
    stats = backfill()
    print(f"Inserted:           {stats['inserted']}")
    print(f"Skipped (empty):    {stats['skipped_empty']}")
    print(f"Skipped (conflict): {stats['skipped_conflict']}")
    print(f"Total collisions:   {stats['total_collisions']}")
    if stats["collisions_sample"]:
        print("\nFirst collisions (kind, normalized, existing_client, attempted_client):")
        for c in stats["collisions_sample"]:
            print(f"  {c[0]:<10} {c[1][:50]:<52} #{c[2]} ↔ #{c[3]}")
