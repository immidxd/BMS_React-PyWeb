"""
Аудит достовірності даних «продано / в наявності».

Виявляє два класи пошкоджень, через які інформація про товар стає
недостовірною (саме такий баг був на #Ф2593):

  1. OVERSOLD — живий sold_count > quantity. Товар показує більше продажів,
     ніж фізично існує пар (фантомна наявність / перепродаж).

  2. PNUM-MISMATCH — одиничне ОПЛАЧЕНЕ замовлення, чиї notes (журнальний текст
     «… ФNNNN (розмір)») називають ІНШИЙ номер товару, ніж той, до якого
     прив'язаний order_item. Це точна сигнатура фантомного мис-лінку:
     legacy-імпорт приклеїв продаж товару Y до товару X. Саме так order 41476
     (продаж Ф2596) опинився на #Ф2593.

Скрипт READ-ONLY — нічого не змінює. Призначений:
  • запускатися автоматично після кожного парсингу замовлень
    (routers/parsing.py викликає audit_summary_line());
  • запускатися вручну для повного звіту:

        python -m backend.scripts.integrity_audit          # короткий підсумок
        python -m backend.scripts.integrity_audit --full   # перелік усіх рядків

⚠️ Формула sold_count має збігатися з канонічною у services/product_service.py
   (Подарунок(7) АБО (Підтверджено(1) І Оплачено); Повернення(9) кредитує сток
   per-client через LEAST(paid, returns)). Якщо там зміниться формула — синхронь
   і тут. Див. пам'ять feedback_sold_availability_semantics.
"""
from __future__ import annotations

import sys
from typing import Optional

from sqlalchemy import text

try:
    from backend.models.database import engine
except ImportError:  # запуск з каталогу backend/
    from models.database import engine


# Канонічний sold_count (дзеркало services/product_service.py). Тримати в синхроні!
_SOLD_CTE = """
    sold AS (
        SELECT pc.product_id,
               GREATEST(SUM(pc.paid_sold) - SUM(LEAST(pc.paid_sold, pc.returns)), 0) AS sold_count
        FROM (
            SELECT oi.product_id, o.client_id,
                   COUNT(*) FILTER (WHERE o.order_status_id = 7
                                      OR (o.order_status_id = 1 AND o.payment_status_id = 1)) AS paid_sold,
                   COUNT(*) FILTER (WHERE o.order_status_id = 9) AS returns
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.product_id IS NOT NULL
              AND o.order_status_id IN (1, 7, 9)
            GROUP BY oi.product_id, o.client_id
        ) pc
        GROUP BY pc.product_id
    )
"""

# Замовлення з РІВНО одним order_item, ОПЛАЧЕНІ (продані), у notes є Ф-токен,
# чий БАЗОВИЙ номер не збігається з базовим номером прив'язаного товару, ПРИЧОМУ
# існує реальний товар із цим Ф-номером. Це сигнатура фантомного мис-лінку (41476).
#
# ⚠️ Порівнюємо БАЗОВІ номери (без ростовкового суфікса `-N`): Ф1075-2 і Ф1075 —
#    той самий товар, не мис-лінк. Інакше — лавина хибних спрацювань на ростовці.
#    Так само ігноруємо рядки, де номер прив'язаного товару ВЗАГАЛІ присутній у
#    notes (тоді інший Ф-номер — це коментар «поміряти ФNNNN», а не проданий товар).
# Це READ-ONLY ФЛАГ для ручного розгляду — НЕ автоматичне видалення.
_PNUM_MISMATCH_SQL = """
    WITH singles AS (
        SELECT o.id AS order_id, o.order_date, o.client_id, o.notes,
               oi.id AS oi_id, oi.product_id,
               upper(regexp_replace(TRIM(LEADING '#' FROM p.productnumber), '-\\d+$', '')) AS linked_base,
               upper(TRIM(LEADING '#' FROM p.productnumber)) AS linked_pnum,
               upper(replace((regexp_matches(o.notes, '[ФфFf]\\s*\\d{2,5}'))[1], ' ', '')) AS noted_pnum
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE (o.order_status_id = 7 OR (o.order_status_id = 1 AND o.payment_status_id = 1))
          AND o.notes ~ '[ФфFf]\\s*\\d{2,5}'
          AND (SELECT COUNT(*) FROM order_items oi2 WHERE oi2.order_id = o.id) = 1
    )
    SELECT s.order_id, s.order_date, s.client_id, s.oi_id,
           s.product_id, s.linked_pnum, s.noted_pnum,
           (SELECT min(p2.id) FROM products p2
             WHERE upper(TRIM(LEADING '#' FROM p2.productnumber)) = s.noted_pnum) AS noted_product_id
    FROM singles s
    WHERE s.noted_pnum <> s.linked_base                       -- різні БАЗОВІ номери
      AND upper(s.notes) !~ ('Ф' || regexp_replace(s.linked_base, '^Ф', ''))  -- linked НЕ згаданий у notes
      AND EXISTS (SELECT 1 FROM products p2
                   WHERE upper(TRIM(LEADING '#' FROM p2.productnumber)) = s.noted_pnum)
    ORDER BY s.order_date
"""

_OVERSOLD_SQL = f"""
    WITH {_SOLD_CTE}
    SELECT p.id, p.productnumber, p.sizeeu,
           COALESCE(NULLIF(p.quantity, 0), 1) AS qty,
           sold.sold_count
    FROM sold
    JOIN products p ON p.id = sold.product_id
    WHERE sold.sold_count > COALESCE(NULLIF(p.quantity, 0), 1)
    ORDER BY (sold.sold_count - COALESCE(NULLIF(p.quantity, 0), 1)) DESC, p.id
"""


def run_audit(conn) -> dict:
    """Повертає {'oversold': [...], 'pnum_mismatch': [...]} (read-only)."""
    oversold = [dict(r._mapping) for r in conn.execute(text(_OVERSOLD_SQL))]
    mismatch = [dict(r._mapping) for r in conn.execute(text(_PNUM_MISMATCH_SQL))]
    return {"oversold": oversold, "pnum_mismatch": mismatch}


def audit_summary_line() -> str:
    """Однорядковий підсумок для логів парсера / job.logs_head."""
    with engine.connect() as conn:
        res = run_audit(conn)
    n_over = len(res["oversold"])
    n_mis = len(res["pnum_mismatch"])
    over_units = sum(int(r["sold_count"]) - int(r["qty"]) for r in res["oversold"])
    if n_over == 0 and n_mis == 0:
        return "[integrity] OK: 0 oversold, 0 pnum-mismatch"
    return (f"[integrity] ⚠️ oversold={n_over} prod (+{over_units} фантом. од.); "
            f"phantom mis-link кандидатів={n_mis} "
            f"(перевір: python -m backend.scripts.integrity_audit --full)")


def main(argv: Optional[list] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    full = "--full" in argv
    with engine.connect() as conn:
        res = run_audit(conn)

    over, mis = res["oversold"], res["pnum_mismatch"]
    over_units = sum(int(r["sold_count"]) - int(r["qty"]) for r in over)

    print("=" * 64)
    print("АУДИТ ДОСТОВІРНОСТІ ПРОДАЖІВ / НАЯВНОСТІ")
    print("=" * 64)
    print(f"OVERSOLD товарів:           {len(over)}  (+{over_units} фантомних одиниць)")
    print(f"PNUM-MISMATCH (фантом-лінк): {len(mis)}  кандидатів на розгляд")
    print()

    if full and mis:
        print("--- PNUM-MISMATCH (одиничні оплачені; notes≠прив'язаний товар) ---")
        for r in mis:
            print(f"  order={r['order_id']} date={r['order_date']} client={r['client_id']} "
                  f"oi={r['oi_id']}  linked=#{r['linked_pnum']} (id={r['product_id']}) "
                  f"→ notes={r['noted_pnum']} (реальний id={r['noted_product_id']})")
        print()
    if full and over:
        print("--- OVERSOLD (live sold_count > quantity) ---")
        for r in over:
            print(f"  id={r['id']} {r['productnumber']} розмір={r['sizeeu']} "
                  f"продано={r['sold_count']} з {r['qty']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
