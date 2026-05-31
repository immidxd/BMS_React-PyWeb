"""Helpers для роботи з client_contacts (many-to-one канали клієнта).

Identity-резолв (`find_client_by_contact`) і upsert (`upsert_contact`) —
єдина точка входу для парсера й сервісів. Primary-колонки `clients.*` тримаються
синхронізованими через тригер `client_contacts_sync_primary` (див. міграцію
2026_05_25_002_client_contacts_sync_trigger.sql).

Парсер ходить сюди першим стейджем — це закриває «петлю мерджу», коли у людини
два FB або два phone і single-column модель clients.* змушувала створювати клона.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.utils.identity_normalizer import (
    normalize_phone,
    normalize_facebook,
    normalize_telegram,
    normalize_instagram,
    normalize_email,
)


_NORMALIZERS = {
    "phone":     normalize_phone,
    "viber":     normalize_phone,
    "facebook":  normalize_facebook,
    "telegram":  normalize_telegram,
    "instagram": normalize_instagram,
    "email":     normalize_email,
    # olx/tiktok/messenger: нема канонічного нормалізатора → lowercased trim
    "olx":       None,
    "tiktok":    None,
    "messenger": None,
}

_INVALID = {"#N/A", "#REF!", "#VALUE!", "#ERROR!", "#NAME?", "#NULL!", "#DIV/0!", "#NUM!"}


def _is_garbage(v: str) -> bool:
    s = (v or "").strip().upper()
    return not s or s in _INVALID or s == "ㅤ"


def normalize_contact(kind: str, raw: str) -> Optional[str]:
    if _is_garbage(raw):
        return None
    fn = _NORMALIZERS.get(kind)
    if fn is not None:
        return fn(raw) or (raw or "").strip().lower() or None
    return (raw or "").strip().lower() or None


def find_client_by_contact(session: Session, kind: str, raw: str) -> Optional[int]:
    """Повертає client_id, якщо такий (kind, normalized) уже існує."""
    norm = normalize_contact(kind, raw)
    if not norm:
        return None
    row = session.execute(text("""
        SELECT client_id FROM client_contacts
         WHERE kind = :k AND normalized = :n
         LIMIT 1
    """), {"k": kind, "n": norm}).first()
    return row[0] if row else None


def find_client_by_any_contact(session: Session, channels: dict) -> Optional[tuple[int, str]]:
    """Пошук client_id по будь-якому із заданих каналів. Перший збіг виграє.

    channels = {'phone': '+380...', 'facebook': 'facebook.com/...', ...}
    Повертає (client_id, matched_kind) або None.
    """
    for kind, raw in channels.items():
        cid = find_client_by_contact(session, kind, raw or "")
        if cid:
            return (cid, kind)
    return None


def upsert_contact(session: Session, client_id: int, kind: str, raw: str,
                   source: str = "parser", make_primary: bool = False) -> Optional[int]:
    """Idempotent upsert каналу клієнта.

    Якщо normalized уже належить ІНШОМУ client_id — НЕ перетягуємо власника
    (це сигнал реального дубля; його має вирішити merge UI). Повертаємо None.
    Якщо собі — оновлюємо last_seen_at, опціонально промотуємо до primary.
    Якщо ще нема — INSERT.
    """
    norm = normalize_contact(kind, raw)
    if not norm:
        return None

    owner = session.execute(text("""
        SELECT id, client_id FROM client_contacts
         WHERE kind = :k AND normalized = :n
         LIMIT 1
    """), {"k": kind, "n": norm}).first()

    if owner and owner[1] != client_id:
        # колізія з іншим клієнтом — лишаємо як є, нехай вилазить у каруселі дублів
        return None

    if owner:
        contact_id = owner[0]
        session.execute(text("""
            UPDATE client_contacts
               SET last_seen_at = NOW(),
                   value = CASE WHEN length(:v) > 0 THEN :v ELSE value END
             WHERE id = :id
        """), {"id": contact_id, "v": (raw or "").strip()})
    else:
        contact_id = session.execute(text("""
            INSERT INTO client_contacts
                (client_id, kind, value, normalized, is_primary, source)
            VALUES (:cid, :k, :v, :n, FALSE, :src)
            RETURNING id
        """), {"cid": client_id, "k": kind, "v": (raw or "").strip(),
               "n": norm, "src": source}).scalar()

    if make_primary:
        # знімаємо primary з інших цього ж kind для цього клієнта
        session.execute(text("""
            UPDATE client_contacts
               SET is_primary = FALSE
             WHERE client_id = :cid AND kind = :k AND id <> :id AND is_primary = TRUE
        """), {"cid": client_id, "k": kind, "id": contact_id})
        session.execute(text("""
            UPDATE client_contacts SET is_primary = TRUE WHERE id = :id
        """), {"id": contact_id})

    return contact_id
