"""Single source of truth for every Google Sheets parsing mode exposed by BMS.

This module must stay import-side-effect free: routers, UI metadata and tests can
inspect it without opening DB connections, log files or Google credentials.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


SHEETS_MODE_ROUTES: Dict[str, Tuple[str, str]] = {
    "sheets_products_quick": ("products", "quick"),
    "sheets_products_full": ("products", "full"),
    "sheets_orders_quick": ("orders", "quick"),
    "sheets_orders_full": ("orders", "full"),
    "sheets_full_quick": ("full", "quick"),
    "sheets_full_full": ("full", "full"),
    "sheets_workspace": ("workspace", "quick"),
}


PARSING_MODES: List[dict] = [
    {
        "id": "sheets_products_quick",
        "name": "Товари — швидко (30 аркушів)",
        "description": "Парсинг товарів з Google Sheets «Журнал» — останні 30 партій",
        "icon": "⚡",
        "estimated_time": "~2 хвилини",
    },
    {
        "id": "sheets_products_full",
        "name": "Товари — повний",
        "description": "Парсинг усіх партій товарів з Google Sheets «Журнал»",
        "icon": "📦",
        "estimated_time": "~6 хвилин",
    },
    {
        "id": "sheets_orders_quick",
        "name": "Замовлення — швидко (30 аркушів)",
        "description": "Парсинг замовлень з Google Sheets «Замовлення» — останні 30 аркушів",
        "icon": "🛒",
        "estimated_time": "~2 хвилини",
    },
    {
        "id": "sheets_orders_full",
        "name": "Замовлення — повний",
        "description": "Парсинг усіх замовлень з Google Sheets «Замовлення»",
        "icon": "🛒",
        "estimated_time": "~6 хвилин",
    },
    {
        "id": "sheets_full_quick",
        "name": "Все — швидко (товари + замовлення)",
        "description": "Швидкий парсинг і товарів, і замовлень (останні 30 аркушів кожного)",
        "icon": "🔄",
        "estimated_time": "~4 хвилини",
    },
    {
        "id": "sheets_full_full",
        "name": "Все — повний парсинг",
        "description": "Повний парсинг усіх товарів і замовлень з Google Sheets",
        "icon": "🔄",
        "estimated_time": "~12 хвилин",
    },
    {
        "id": "sheets_workspace",
        "name": "Воркспейс — злиття / додавання",
        "description": "Парсинг Воркспейс1: товари зі збігом ≥4 з 5 характеристик зливаються (номер → клони), решта додаються як нові (без номеру → '???')",
        "icon": "🔀",
        "estimated_time": "~1-2 хвилини",
    },
]


def get_parsing_modes() -> List[dict]:
    """Return copies so callers cannot mutate global mode metadata."""
    return [dict(item) for item in PARSING_MODES]
