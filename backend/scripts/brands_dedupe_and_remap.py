#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для виявлення дублікатів брендів за normalized_name, ремап продуктів на канонічний бренд
та безпечного видалення зайвих брендів. Підтримує dry-run і фільтр ID-діапазону.
"""

import os
import argparse
from typing import Dict, List, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv


def connect_db():
    load_dotenv()
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def fetch_brand_clusters(cur, only_range: Tuple[int, int] = None):
    if only_range:
        cur.execute(
            """
            select id, brandname, normalized_name
            from brands
            where id between %s and %s
            order by normalized_name nulls last, id
            """,
            (only_range[0], only_range[1])
        )
    else:
        cur.execute(
            "select id, brandname, normalized_name from brands order by normalized_name nulls last, id"
        )
    rows = cur.fetchall()
    clusters: Dict[str, List[Dict]] = {}
    for r in rows:
        nn = r["normalized_name"]
        if not nn:
            nn = f"__NULL__::{r['brandname'] or ''}"
        clusters.setdefault(nn, []).append(r)
    return clusters


def choose_canonical(cluster: List[Dict]) -> Dict:
    # Вибираємо найменший id як канонічний
    return sorted(cluster, key=lambda r: r["id"])[0]


def main():
    parser = argparse.ArgumentParser(description="Дедуплікація брендів та ремап продуктів")
    parser.add_argument("--dry", action="store_true", help="Лише показати план змін")
    parser.add_argument("--range", type=str, help="Діапазон ID, наприклад 5..70")
    args = parser.parse_args()

    only_range = None
    if args.range:
        a, b = args.range.split("..")
        only_range = (int(a), int(b))

    conn = connect_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    clusters = fetch_brand_clusters(cur, only_range)
    duplicates = {k: v for k, v in clusters.items() if len(v) > 1}

    if not duplicates:
        print("Дублікатів не знайдено")
        return

    total_remap = 0
    total_deleted = 0

    for nn, cluster in duplicates.items():
        canon = choose_canonical(cluster)
        others = [r for r in cluster if r["id"] != canon["id"]]
        print(f"normalized_name='{nn}': canonical id={canon['id']} ({canon['brandname']}), others={[r['id'] for r in others]}")

        # Ремап продуктів
        cur.execute(
            "select count(*) as cnt from products where brandid = any(%s)",
            ([r["id"] for r in others],)
        )
        cnt = cur.fetchone()["cnt"]
        if cnt > 0:
            print(f"  Перепризначаємо {cnt} продуктів на brandid={canon['id']}")
            if not args.dry:
                cur.execute(
                    "update products set brandid=%s where brandid = any(%s)",
                    (canon["id"], [r["id"] for r in others])
                )
                conn.commit()
                total_remap += cnt

        # Видалення інших брендів
        print(f"  Безпечне видалення брендів: {[r['id'] for r in others]}")
        if not args.dry:
            cur.execute(
                "delete from brands where id = any(%s)",
                ([r["id"] for r in others],)
            )
            conn.commit()
            total_deleted += len(others)

    print(f"Готово. Перепризначено продуктів: {total_remap}, видалено брендів: {total_deleted}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()


