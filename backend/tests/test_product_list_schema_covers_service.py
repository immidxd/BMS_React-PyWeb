"""`response_model` мовчки зрізає все, чого немає у схемі.

30.09.2026: сервіс рахував `proposals_count` для кожного рядка, у відповіді
`/api/products` його не було ЖОДНОГО разу — тож кнопки «✓ N» у рядку таблиці
й «Підтвердити все» у картці завозу не показувались взагалі. Ані помилки,
ані попередження: FastAPI просто викидає зайве поле.

Сторож структурний: беремо КЛЮЧІ словника, який `get_products` кладе в
`items`, і вимагаємо, щоб кожен був полем `ProductList`. Нове поле тепер не
може тихо загубитись між сервісом і схемою.
"""
from __future__ import annotations

import ast
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from schemas.product import ProductList  # noqa: E402


def _item_keys() -> set[str]:
    """Ключі літерала `product_dict = {...}` усередині get_products."""
    tree = ast.parse((BACKEND / "services" / "product_service.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "get_products")
    for node in ast.walk(fn):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                and any(getattr(t, "id", None) == "product_dict" for t in node.targets)):
            return {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    raise AssertionError("не знайшов літерал product_dict у get_products")


def test_every_field_the_service_returns_survives_the_response_model():
    missing = sorted(_item_keys() - set(ProductList.model_fields))
    assert not missing, (
        "ці поля сервіс рахує, а response_model викидає з JSON — "
        f"додайте їх у schemas.product.ProductList: {missing}"
    )


def test_proposals_count_is_the_regression_that_started_this():
    assert "proposals_count" in ProductList.model_fields
    assert "proposals_count" in _item_keys()
