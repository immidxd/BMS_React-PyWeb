"""«Додати товар у завіз» імпортує хелпери парсера ВСЕРЕДИНІ обробника.

Такий імпорт не падає при старті застосунку — лише при першому натисканні
«Зберегти товар». 04.09 `_apply_product_materials` перейменували на
`_apply_product_relations`, і форма мовчки віддавала 500 до 23.09. Цей тест
ловить розрив одразу: кожне імʼя, яке обробник тягне зі сторонніх модулів,
мусить існувати.
"""
import ast
import importlib
import inspect
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from routers import deliveries  # noqa: E402


def _handler_imports():
    src = inspect.getsource(deliveries.add_product_to_delivery)
    tree = ast.parse(src.lstrip() if not src.startswith("@") else src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.module.startswith("backend."):
            out.extend((node.module, a.name) for a in node.names)
    return out


def test_handler_has_imports():
    assert _handler_imports(), "очікували локальні імпорти в обробнику"


@pytest.mark.parametrize("module,name", _handler_imports())
def test_quick_add_imported_names_exist(module, name):
    mod = importlib.import_module(module)
    if not hasattr(mod, name):  # `from services import label_service` — підмодуль
        try:
            importlib.import_module(f"{module}.{name}")
        except ImportError:
            pass
    assert hasattr(mod, name), f"{module}.{name} не існує — форма «Додати товар» впаде з 500"


def test_technology_name_not_resolved_as_scalar_lookup():
    """technology_name — many-to-many; у LOOKUP_NAME_FIELDS його немає, тож
    обробник не має шукати його там (KeyError = 500 при заповнених технологіях)."""
    src = inspect.getsource(deliveries.add_product_to_delivery)
    loop = src.split("for nf in (", 1)[1].split(")", 1)[0]
    assert "technology_name" not in loop
