#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утиліти для нормалізації брендів та безпечного upsert у таблицю brands.
"""

import re
import unicodedata
from typing import Optional

import psycopg2
from psycopg2.extensions import connection as _PGConn
from psycopg2.extensions import cursor as _PGCursor


# Кирилиця, візуально НЕВІДРІЗНЯЛЬНА від латиниці. При ручному записі назви
# бренду в журнал легко лишити кириличну розкладку на першій літері: 'Кappa'
# (кирилична К), 'Сrocs', 'Тamaris', 'ЕССО'. Око різниці не бачить, а для БД це
# інший бренд — звідси дублі в brands і фантомні товари '-2' під тим самим лотом.
# Згортаємо ЛИШЕ справжні двійники; решта кирилиці лишається як є, тож питомо
# кириличні назви не страждають.
_HOMOGLYPH_FOLD = str.maketrans({
    "а": "a", "в": "b", "е": "e", "ё": "e", "і": "i", "ј": "j", "к": "k",
    "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "у": "y",
    "х": "x", "ѕ": "s", "ԁ": "d", "ԝ": "w",
})


def normalize_brand(raw_name: Optional[str]) -> Optional[str]:
    """Повертає нормалізовану назву бренду:
    - trim
    - до нижнього регістру
    - звести кириличні візуальні двійники до латиниці ('Кappa' → 'kappa')
    - видалити діакритики (залишити базові символи)
    - замінити будь-які послідовності пробілів/розділових на один пробіл
    - прибрати крапки, коми, дефіси, підкреслення, лапки, слеші, крапки з комою
    """
    if not raw_name:
        return None

    name = str(raw_name).strip().lower().translate(_HOMOGLYPH_FOLD)
    if not name:
        return None

    # Видалити діакритики, залишивши базові символи (включно з кирилицею)
    # Для кирилиці зазвичай немає діакритиків, але NFKD + фільтр комбінуючих безпечний
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))

    # Замінити будь-які небуквено-цифрові символи на пробіли
    normalized = re.sub(r"[^\w\u0400-\u04FF]+", " ", normalized, flags=re.UNICODE)

    # Колапс пробілів
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized or None


def is_blocked_brand(cur: _PGCursor, normalized_name: Optional[str]) -> bool:
    """Перевіряє чи бренд заблокований (у brand_blocklist). Якщо таблиці немає – вважаємо не заблокованим."""
    if not normalized_name:
        return False
    try:
        cur.execute(
            "SELECT 1 FROM brand_blocklist WHERE normalized_name = %s",
            (normalized_name,)
        )
        return cur.fetchone() is not None
    except psycopg2.Error:
        # Таблиці може не бути ще – не блокуємо
        cur.connection.rollback()
        return False


def upsert_brand_and_get_id(cur: _PGCursor, conn: _PGConn, raw_brand_name: Optional[str]) -> Optional[int]:
    """Вставляє/оновлює бренд за normalized_name. Якщо у блоклисті – повертає None.

    Використовує унікальний індекс по brands.normalized_name (створюється міграцією).
    При конфлікті оновлює лише display-ім'я (brandname), щоб зберегти канонічний normalized_name.
    """
    if not raw_brand_name:
        return None

    try:
        from backend.services.brand_normalization import canonicalize_brand_name
    except ImportError:
        try:
            from services.brand_normalization import canonicalize_brand_name
        except ImportError:  # standalone legacy environments
            canonicalize_brand_name = lambda value: str(value).strip() if value else None

    display_name = canonicalize_brand_name(str(raw_brand_name).strip()) or ""
    if not display_name:
        return None
    normalized_name = normalize_brand(display_name)
    if not normalized_name:
        return None

    # Блок-лист
    if is_blocked_brand(cur, normalized_name):
        return None

    # Перевірити наявний запис
    try:
        cur.execute(
            "SELECT id FROM brands WHERE normalized_name = %s",
            (normalized_name,)
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
    except psycopg2.Error:
        conn.rollback()
        # Якщо колонки normalized_name ще нема (до міграції) – fallback на brandname точним збігом
        cur.execute(
            "SELECT id FROM brands WHERE brandname = %s",
            (display_name,)
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        # Інакше звичайна вставка без normalized_name
        cur.execute(
            "INSERT INTO brands (brandname) VALUES (%s) RETURNING id",
            (display_name,)
        )
        conn.commit()
        return int(cur.fetchone()[0])

    # Частковий індекс потребує такого самого WHERE у conflict target.
    # Display name при конфлікті НЕ перезаписуємо: випадковий регістр зі старої
    # вкладки не має права зіпсувати вже затверджене канонічне написання.
    cur.execute(
        (
            "INSERT INTO brands (brandname, normalized_name) "
            "VALUES (%s, %s) "
            "ON CONFLICT (normalized_name) WHERE normalized_name IS NOT NULL "
            "DO UPDATE SET normalized_name = EXCLUDED.normalized_name "
            "RETURNING id"
        ),
        (display_name, normalized_name)
    )
    new_id = int(cur.fetchone()[0])
    conn.commit()
    return new_id

