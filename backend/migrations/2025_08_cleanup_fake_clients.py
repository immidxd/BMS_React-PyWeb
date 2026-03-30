"""
Міграція: очистка фейкових клієнтів ("Невідомий клієнт", "Магазин", "Ім'я клієнт").

Що робить:
1. "Невідомий клієнт" (id 8781, 24553) → orders.client_id = NULL, видалити клієнтів
2. "Магазин (walk-in)" (id 39571) → orders.client_id = NULL + sales_channel='Магазин', видалити
3. "Ім'я клієнт" → last_name = NULL (залишаємо клієнта з first_name), об'єднуємо дублі
4. Невидимі символи (ㅤ) → orders.client_id = NULL, видалити
5. Телефон-як-ім'я → переносимо в phone_number

Безпечність:
- Не видаляє замовлення — тільки відв'язує client_id
- Не видаляє клієнтів з реальними замовленнями (окрім placeholder-ів)
- Логує кожну дію для аудиту

Запуск: python -m backend.migrations.2025_08_cleanup_fake_clients
"""
import re
import logging
from sqlalchemy import text
from backend.models.database import SessionLocal, engine

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def run_migration():
    logger.info("=== Початок міграції: очистка фейкових клієнтів ===")

    with engine.begin() as conn:
        # ── 0. Зняти NOT NULL з client_id + додати nickname колонку ──
        conn.execute(text("ALTER TABLE orders ALTER COLUMN client_id DROP NOT NULL"))
        conn.execute(text("ALTER TABLE clients ADD COLUMN IF NOT EXISTS nickname VARCHAR(255)"))
        logger.info("✓ orders.client_id тепер nullable, nickname колонка додана")

        # ── 1. "Невідомий клієнт" → відв'язати замовлення, видалити клієнтів ──
        # Використовуємо прямі ID + загальний патерн
        fake_ids = conn.execute(text("""
            SELECT id FROM clients
            WHERE (first_name = 'Невідомий' AND coalesce(last_name, '') IN ('клієнт', ''))
               OR id IN (8781, 24553)
        """)).fetchall()
        fake_client_ids = [r[0] for r in fake_ids]
        if fake_client_ids:
            result = conn.execute(text("""
                UPDATE orders SET client_id = NULL
                WHERE client_id = ANY(:ids)
            """), {"ids": fake_client_ids})
            logger.info(f"✓ Відв'язано {result.rowcount} замовлень від 'Невідомий клієнт'")
            conn.execute(text("""
                DELETE FROM clients WHERE id = ANY(:ids)
                  AND id NOT IN (SELECT DISTINCT client_id FROM orders WHERE client_id IS NOT NULL)
            """), {"ids": fake_client_ids})
            logger.info(f"✓ Видалено записи 'Невідомий клієнт'")

        # ── 2. "Магазин (walk-in)" → sales_channel='Магазин', відв'язати, видалити ──
        shop_ids = conn.execute(text("""
            SELECT id FROM clients
            WHERE first_name = 'Магазин' OR id = 39571
        """)).fetchall()
        shop_client_ids = [r[0] for r in shop_ids]
        if shop_client_ids:
            result = conn.execute(text("""
                UPDATE orders SET client_id = NULL, sales_channel = 'Магазин'
                WHERE client_id = ANY(:ids)
            """), {"ids": shop_client_ids})
            logger.info(f"✓ Відв'язано {result.rowcount} замовлень від 'Магазин' (sales_channel → 'Магазин')")
            conn.execute(text("""
                DELETE FROM clients WHERE id = ANY(:ids)
                  AND id NOT IN (SELECT DISTINCT client_id FROM orders WHERE client_id IS NOT NULL)
            """), {"ids": shop_client_ids})
            logger.info(f"✓ Видалено записи 'Магазин'")

        # ── 3. Невидимі символи (ㅤ, порожні) → відв'язати, видалити ──
        invis_ids = conn.execute(text("""
            SELECT id FROM clients
            WHERE regexp_replace(coalesce(first_name, ''), '[\u3164\u2800\s]', '', 'g') = ''
              AND regexp_replace(coalesce(last_name, ''), '[\u3164\u2800\s]', '', 'g') = ''
        """)).fetchall()
        invis_client_ids = [r[0] for r in invis_ids]
        if invis_client_ids:
            result = conn.execute(text("""
                UPDATE orders SET client_id = NULL WHERE client_id = ANY(:ids)
            """), {"ids": invis_client_ids})
            logger.info(f"✓ Відв'язано {result.rowcount} замовлень від клієнтів з невидимими символами")
            conn.execute(text("""
                DELETE FROM clients WHERE id = ANY(:ids)
                  AND id NOT IN (SELECT DISTINCT client_id FROM orders WHERE client_id IS NOT NULL)
            """), {"ids": invis_client_ids})
            logger.info(f"✓ Видалено записи з невидимими символами")

        # ── 4. "Ім'я клієнт" → очистити last_name, перенести телефони ──
        # 4a. Телефон-як-ім'я → перенести в phone_number
        result = conn.execute(text("""
            UPDATE clients
            SET phone_number = CASE
                    WHEN phone_number IS NULL OR phone_number = '' THEN first_name
                    ELSE phone_number
                END,
                first_name = NULL
            WHERE lower(trim(coalesce(last_name, ''))) = 'клієнт'
              AND first_name ~ '^[0-9+]{10,}'
        """))
        logger.info(f"✓ Перенесено {result.rowcount} телефонів з first_name у phone_number")

        # 4b. Прибираємо невидимі символи з імені
        conn.execute(text("""
            UPDATE clients
            SET first_name = regexp_replace(first_name, '[\u3164\u2800]', '', 'g')
            WHERE first_name ~ '[\u3164\u2800]'
        """))

        # 4c. Очищуємо last_name = 'клієнт' → NULL
        result = conn.execute(text("""
            UPDATE clients
            SET last_name = NULL
            WHERE lower(trim(coalesce(last_name, ''))) = 'клієнт'
        """))
        logger.info(f"✓ Очищено last_name='клієнт' у {result.rowcount} записах")

        # ── 5. Об'єднання дублікатів "Ім'я клієнт" (тепер "Ім'я" з last_name=NULL) ──
        # Знаходимо дублікати за first_name де last_name IS NULL
        dupes = conn.execute(text("""
            SELECT lower(trim(first_name)) AS fname, array_agg(id ORDER BY id) AS ids
            FROM clients
            WHERE first_name IS NOT NULL
              AND (last_name IS NULL OR last_name = '')
              AND lower(trim(first_name)) != ''
            GROUP BY lower(trim(first_name))
            HAVING COUNT(*) > 1
        """)).fetchall()

        total_merged = 0
        for row in dupes:
            fname = row[0]
            ids = row[1]
            keep_id = ids[0]  # Зберігаємо найстарший запис
            merge_ids = ids[1:]

            # Перев'язуємо замовлення дублікатів до основного запису
            for merge_id in merge_ids:
                conn.execute(text("""
                    UPDATE orders SET client_id = :keep_id
                    WHERE client_id = :merge_id
                """), {"keep_id": keep_id, "merge_id": merge_id})

                # Збагачуємо контактні дані основного запису
                conn.execute(text("""
                    UPDATE clients dst
                    SET phone_number = COALESCE(NULLIF(dst.phone_number, ''), src.phone_number),
                        email = COALESCE(NULLIF(dst.email, ''), src.email),
                        facebook = COALESCE(NULLIF(dst.facebook, ''), src.facebook),
                        viber = COALESCE(NULLIF(dst.viber, ''), src.viber),
                        telegram = COALESCE(NULLIF(dst.telegram, ''), src.telegram),
                        instagram = COALESCE(NULLIF(dst.instagram, ''), src.instagram)
                    FROM clients src
                    WHERE dst.id = :keep_id AND src.id = :merge_id
                """), {"keep_id": keep_id, "merge_id": merge_id})

                # Видаляємо дублікат
                conn.execute(text("DELETE FROM clients WHERE id = :id"), {"id": merge_id})
                total_merged += 1

        logger.info(f"✓ Об'єднано {total_merged} дублікатів клієнтів")

        # ── 6. Видалити порожніх клієнтів без замовлень ──
        result = conn.execute(text("""
            DELETE FROM clients
            WHERE coalesce(first_name, '') = ''
              AND coalesce(last_name, '') = ''
              AND coalesce(nickname, '') = ''
              AND id NOT IN (SELECT DISTINCT client_id FROM orders WHERE client_id IS NOT NULL)
        """))
        logger.info(f"✓ Видалено {result.rowcount} порожніх клієнтів без замовлень")

        # ── 7. Фінальна статистика ──
        stats = conn.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM clients) AS total_clients,
                (SELECT COUNT(*) FROM orders) AS total_orders,
                (SELECT COUNT(*) FROM orders WHERE client_id IS NULL) AS anonymous_orders
        """)).fetchone()

        logger.info(f"=== Результат міграції ===")
        logger.info(f"  Клієнтів: {stats[0]}")
        logger.info(f"  Замовлень: {stats[1]}")
        logger.info(f"  Анонімних замовлень: {stats[2]}")
        logger.info(f"=== Міграція завершена успішно ===")


if __name__ == "__main__":
    run_migration()
