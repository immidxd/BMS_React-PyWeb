"""Small, side-effect-free safeguards shared by the Sheets orders parser."""

from __future__ import annotations

from typing import Any, Optional


def fill_missing_order_client(order: Any, parsed_client_id: Optional[int]) -> bool:
    """Attach an unambiguous parsed client only when the order has none.

    Existing client links are never replaced automatically: a different client
    in Sheets may indicate a row move or identity ambiguity and needs review.
    """
    if getattr(order, "client_id", None) is None and parsed_client_id is not None:
        order.client_id = parsed_client_id
        return True
    return False
