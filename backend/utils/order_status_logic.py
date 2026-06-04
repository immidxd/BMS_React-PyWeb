"""
Central definitions for order-status semantics.

Why this module exists
──────────────────────
The order_statuses table has 11 entries, and historically the codebase used raw
status IDs (e.g. ``order_status_id NOT IN (5, 6, 9)``) scattered across many SQL
queries. Different callers used different exclusion sets without a shared
vocabulary, which led to bugs like:

  • "Продані, але висять" flagging products whose latest order was just
    "В черзі" (queued) — because the helper treated everything except a
    narrow cancelled-set as "sold".

  • Revenue queries including statuses like "Подарунок" (gifted) that don't
    generate revenue.

To prevent recurrence, every place that interprets an order status MUST use
one of the named sets below. If a new business question doesn't fit any of
them, add a new named set here with a clear docstring — do NOT inline a magic
number list at the call site.

Status reference (from `order_statuses` table, do not renumber)
───────────────────────────────────────────────────────────────
  1  Підтверджено  — confirmed sale; payment expected / received
  2  Очікується    — awaiting client decision
  3  Уточнити      — needs clarification with client
  4  Фото          — client asked for more photos
  5  Відміна       — cancelled; stock returns to shelf
  6  Ігнорування   — client unresponsive; effectively cancelled
  7  Подарунок     — given as a gift; stock consumed but no revenue
  8  В черзі       — queued; client wants but not yet committed
  9  Повернення    — returned by client; stock returns to shelf
 10  Обмін         — exchange in progress
 11  Передати      — handover to another channel/person
"""

from __future__ import annotations
from typing import Iterable

# ── Individual status IDs ────────────────────────────────────────────────────
STATUS_CONFIRMED   = 1   # Підтверджено
STATUS_AWAITING    = 2   # Очікується
STATUS_CLARIFY     = 3   # Уточнити
STATUS_PHOTO       = 4   # Фото
STATUS_CANCELLED   = 5   # Відміна
STATUS_IGNORED     = 6   # Ігнорування
STATUS_GIFT        = 7   # Подарунок
STATUS_QUEUED      = 8   # В черзі
STATUS_RETURNED    = 9   # Повернення
STATUS_EXCHANGE    = 10  # Обмін
STATUS_HANDOFF     = 11  # Передати


# ── Named semantic groups ────────────────────────────────────────────────────
#
# CONFIRMED_SOLD: the sale actually happened. Use this for "is this product
# truly sold?" decisions — sold_count metrics, "Продані, але висять" filter,
# product status display.
#
# Notes:
#  • Includes 7=Подарунок because the unit physically left the shelf.
#  • Excludes everything pending (2/3/4/8/10/11) — those reserve stock but the
#    transaction is not complete.
#
# ⚠️ sold_count refinement (2026-06-04): a unit counts as SOLD only when
#   • status = 7 (Подарунок), OR
#   • status = 1 (Підтверджено) AND payment_status_id = 1 (Оплачено).
# i.e. a confirmed-but-UNPAID order does NOT consume stock (it's a pending sale).
# product_service.get_products applies this in its sold_count / last_sale / sibling
# subqueries. So `STATUS_CONFIRMED in CONFIRMED_SOLD` is necessary but NOT
# sufficient for sold_count — the payment check rides alongside it.
PAID_STATUS_ID: int = 1   # payment_statuses.id == 'Оплачено'
CONFIRMED_SOLD: tuple[int, ...] = (STATUS_CONFIRMED, STATUS_GIFT)


# RESERVED: the order ties up a stock unit even though the sale isn't final.
# Use this for "is any unit of this size/variant still available right now?"
# checks — sibling-stock lookups, availability heatmaps.
#
# Excludes 5/6/9 (Відміна/Ігнорування/Повернення — stock back on shelf) and
# 0/NULL (no order at all).
RESERVED: tuple[int, ...] = (
    STATUS_CONFIRMED, STATUS_AWAITING, STATUS_CLARIFY, STATUS_PHOTO,
    STATUS_GIFT, STATUS_QUEUED, STATUS_EXCHANGE, STATUS_HANDOFF,
)


# CANCELLED_OR_RETURNED: the order is dead — stock has been returned to the
# pool. Use as a "skip these" filter when summing real transactions.
CANCELLED_OR_RETURNED: tuple[int, ...] = (
    STATUS_CANCELLED, STATUS_IGNORED, STATUS_RETURNED,
)


# REVENUE_GENERATING: orders that should count toward revenue figures.
# Conservative definition: only Підтверджено produces revenue. Подарунок
# consumes stock but generates none. Pending/queued aren't paid yet, so they
# don't count until they become Підтверджено.
#
# Note: as of this commit, backend/routers/statistics.py still uses a wider
# `!= 5` rule for revenue. That's a separate audit item — see module-top
# audit notes if you're cleaning that up.
REVENUE_GENERATING: tuple[int, ...] = (STATUS_CONFIRMED,)


# CLIENT_ENGAGEMENT: counts toward "real orders this client has placed"
# metrics. Excludes outright cancellations and returns/exchanges (those are
# attempts that didn't stick), but keeps Подарунок and pending statuses
# because the client did engage.
CLIENT_ENGAGEMENT: tuple[int, ...] = (
    STATUS_CONFIRMED, STATUS_AWAITING, STATUS_CLARIFY, STATUS_PHOTO,
    STATUS_GIFT, STATUS_QUEUED, STATUS_HANDOFF,
)


# ── SQL helpers ──────────────────────────────────────────────────────────────
def sql_in_list(ids: Iterable[int]) -> str:
    """Render a tuple of ints as a SQL ``IN (...)`` list (no leading IN)."""
    return "(" + ", ".join(str(i) for i in ids) + ")"


def latest_order_status_in(pid_ref: str, ids: Iterable[int]) -> str:
    """Build a SQL boolean expression: latest order on product `pid_ref` has
    status in `ids`. `pid_ref` is a SQL fragment naming the product id column
    (e.g. ``"p.id"`` or ``"avail_p.id"``).

    Uses COALESCE(..., 0) so products with no orders evaluate to FALSE.
    """
    in_list = sql_in_list(ids)
    return f"""COALESCE((
        SELECT o.order_status_id
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE oi.product_id = {pid_ref}
        ORDER BY o.created_at DESC LIMIT 1
    ), 0) IN {in_list}"""


def latest_order_confirmed_sold(pid_ref: str) -> str:
    """Latest order on product is a completed sale (Підтверджено or Подарунок)."""
    return latest_order_status_in(pid_ref, CONFIRMED_SOLD)


def latest_order_reserved(pid_ref: str) -> str:
    """Latest order on product reserves the stock unit (any non-cancelled,
    non-returned active order)."""
    return latest_order_status_in(pid_ref, RESERVED)


def product_fully_consumed(pid_ref: str) -> str:
    """SQL boolean: this product has NO available stock left, i.e. the count
    of confirmed-sold order_items meets or exceeds the product's ``quantity``.

    Use this — not :func:`latest_order_confirmed_sold` — when answering "is
    this row sold out?" for multi-unit products (e.g. quantity=3 with only
    one buyer is still 2 units in stock, not "Продано").

    ``pid_ref`` is the SQL fragment naming the product id (e.g. ``"p.id"``).
    Uses COALESCE so a missing quantity defaults to 1 (legacy rows).
    """
    sold_set = sql_in_list(CONFIRMED_SOLD)
    return f"""COALESCE((
        SELECT COUNT(*) FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE oi.product_id = {pid_ref}
          AND o.order_status_id IN {sold_set}
    ), 0) >= COALESCE((
        SELECT NULLIF(quantity, 0) FROM products WHERE id = {pid_ref}
    ), 1)"""
