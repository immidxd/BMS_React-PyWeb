"""
Собівартість (COGS) allocation helpers for statistics.

⚠️ КРИТИЧНО: ``products.price`` — це ПРОДАЖНА ціна, а НЕ закупівля. Реальна
собівартість живе на рівні поставки: ``deliveries.purchase_cost`` (загальна
закупівля всієї поставки) та ``deliveries.delivery_cost`` (доставка). Історично
статистика сумувала ``products.price`` як «собівартість», через що cost ≈ revenue
і прибуток виходив ≈0 або хибно від'ємним (audit 2026-06-15, виторг 12.4M ≈
«собівартість» 12.6M → −343K замість реального плюса ~+5.5M).

Правила розподілу (бізнес-рішення 2026-06-15):
  • Закупівлю поставки ділимо ПОРІВНУ між парами:
        purch_per_pair = purchase_cost / pairs
  • Доставку — так само: ship_per_pair = delivery_cost / pairs
  • Поставки без purchase_cost (~105/423) → собівартість товару оцінюємо як
        selling_price × GLOBAL_RATIO,
    де GLOBAL_RATIO = Σpurchase_cost / Σprice по поставках, де закупівля ВІДОМА
    (≈0.60). Такі товари позначені is_estimated = TRUE.

Використання:
  • ``COST_RATIO_CTE`` — самодостатній CTE ``cost_ratio(ratio)`` (один рядок).
    Беремо, коли потрібен лише коефіцієнт (оцінка собівартості поставки/ордера
    без позицій як total_amount × ratio).
  • ``PRODUCT_COST_CTE`` — включає ``cost_ratio`` + дає CTE
    ``product_cost(product_id, unit_cost, unit_ship, is_estimated)``.
    JOIN-ити по product_id, коли треба собівартість конкретних проданих позицій.

Обидва ставляться ПЕРШИМИ у WITH-блоці (через кому з наступними CTE).
"""

# Глобальний коефіцієнт собівартості (Σзакупівля / Σпродажна по відомих поставках).
# Фолбек-константа 0.6 використовується тільки якщо немає жодної поставки з
# заповненим purchase_cost (порожня БД) — у проді рахується наживо.
COST_RATIO_CTE = """
cost_ratio AS (
    SELECT COALESCE(SUM(d.purchase_cost) / NULLIF(SUM(pp.psum), 0), 0.6) AS ratio
    FROM deliveries d
    JOIN (SELECT deliveryid, SUM(COALESCE(price, 0)) AS psum
          FROM products GROUP BY deliveryid) pp ON pp.deliveryid = d.id
    WHERE d.purchase_cost > 0
)
"""

PRODUCT_COST_CTE = COST_RATIO_CTE + """,
deliv_unit_cost AS (
    SELECT d.id AS delivery_id,
           d.purchase_cost / NULLIF(cnt.n, 0)              AS purch_per_pair,
           COALESCE(d.delivery_cost, 0) / NULLIF(cnt.n, 0) AS ship_per_pair
    FROM deliveries d
    JOIN (SELECT deliveryid, COUNT(*) AS n FROM products GROUP BY deliveryid) cnt
         ON cnt.deliveryid = d.id
    WHERE d.purchase_cost > 0
),
product_cost AS (
    SELECT p.id AS product_id,
           CASE WHEN duc.purch_per_pair IS NOT NULL THEN duc.purch_per_pair
                ELSE COALESCE(p.price, 0) * (SELECT ratio FROM cost_ratio) END AS unit_cost,
           COALESCE(duc.ship_per_pair, 0) AS unit_ship,
           (duc.purch_per_pair IS NULL)   AS is_estimated
    FROM products p
    LEFT JOIN deliv_unit_cost duc ON duc.delivery_id = p.deliveryid
)
"""
