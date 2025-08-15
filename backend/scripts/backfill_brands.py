#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Одноразовий скрипт backfill для заповнення products.brandid на основі текстових полів бренду,
з використанням нормалізації та блоклиста. Підтримує dry-run.
"""

import os
import argparse
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from .brand_utils import upsert_brand_and_get_id, normalize_brand


def connect_db():
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def guess_brand_text(row: dict) -> Optional[str]:
    # Пошук джерела тексту бренду: спробуємо products.model/marking або попередні дублікати
    # Якщо є додаткові поля (наприклад, legacy brand text), додайте тут
    # За замовчуванням – повертаємо None (пропускаємо)
    return None


def main():
    parser = argparse.ArgumentParser(description="Backfill products.brandid")
    parser.add_argument("--batch", type=int, default=1000, help="Розмір пакету")
    parser.add_argument("--dry", action="store_true", help="Dry-run: тільки показати план змін")
    args = parser.parse_args()

    conn = connect_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT p.id, p.brandid, p.model, p.marking
        FROM products p
        WHERE p.brandid IS NULL
        ORDER BY p.id
        """
    )
    rows = cur.fetchall()

    total = len(rows)
    updated = 0
    skipped = 0

    print(f"Знайдено продуктів без бренду: {total}")

    batch_updates = []
    for i, r in enumerate(rows, start=1):
        brand_text = guess_brand_text(r)
        if not brand_text:
            skipped += 1
            continue

        brand_id = upsert_brand_and_get_id(cur, conn, brand_text)
        if not brand_id:
            skipped += 1
            continue

        batch_updates.append((brand_id, r["id"]))

        if len(batch_updates) >= args.batch:
            if args.dry:
                print(f"[DRY] План оновлення {len(batch_updates)} записів (приклад): {batch_updates[:3]}")
                batch_updates.clear()
            else:
                cur.executemany("UPDATE products SET brandid = %s WHERE id = %s", batch_updates)
                conn.commit()
                updated += len(batch_updates)
                batch_updates.clear()

        if i % 1000 == 0:
            print(f"Опрацьовано {i}/{total}")

    # Хвіст
    if batch_updates:
        if args.dry:
            print(f"[DRY] План оновлення {len(batch_updates)} записів (приклад): {batch_updates[:3]}")
        else:
            cur.executemany("UPDATE products SET brandid = %s WHERE id = %s", batch_updates)
            conn.commit()
            updated += len(batch_updates)

    print(f"Готово. Оновлено: {updated}, пропущено: {skipped}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()


