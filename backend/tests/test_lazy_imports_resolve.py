"""Кожен імпорт ВСЕРЕДИНІ функції в routers/ і services/ мусить резолвитись.

Такі імпорти не перевіряються при старті застосунку — лише коли виконується
функція. 04.09 `_apply_product_materials` перейменували, а «Додати товар у
завіз» імпортував стару назву всередині обробника: застосунок стартував, тести
проходили, а кожне «Зберегти товар» до 23.09 віддавало 500.

Тест статично (ast) збирає `from <локальний модуль> import <імʼя>` у тілах
функцій і перевіряє, що імʼя існує. Сам код функцій не виконується.
scripts/ не скануємо цілком (там одноразові скрипти з побічними ефектами при
імпорті), але модулі scripts.*, які тягнуть роутери й сервіси, перевіряються.
"""
import ast
import importlib
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

LOCAL_ROOTS = {"services", "routers", "scripts", "models", "utils", "schemas", "app"}
SCAN_DIRS = ("routers", "services")


def _lazy_imports():
    out = []
    for d in SCAN_DIRS:
        for root, _dirs, files in os.walk(os.path.join(BACKEND, d)):
            if "__pycache__" in root:
                continue
            for fn in sorted(files):
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read())
                rel = os.path.relpath(path, BACKEND)
                for func in ast.walk(tree):
                    if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for node in ast.walk(func):
                        if not (isinstance(node, ast.ImportFrom) and node.module and node.level == 0):
                            continue
                        mod = node.module[len("backend."):] if node.module.startswith("backend.") else node.module
                        if mod.split(".")[0] not in LOCAL_ROOTS:
                            continue
                        for alias in node.names:
                            if alias.name != "*":
                                out.append((f"{rel}:{node.lineno}", mod, alias.name))
    return sorted(set(out))


LAZY = _lazy_imports()


def test_scanner_finds_imports():
    assert len(LAZY) > 100


@pytest.mark.parametrize("where,module,name", LAZY, ids=[f"{w}:{n}" for w, _m, n in LAZY])
def test_lazy_import_resolves(where, module, name):
    mod = importlib.import_module(module)
    if not hasattr(mod, name):  # `from services import label_service` — підмодуль
        try:
            importlib.import_module(f"{module}.{name}")
        except ImportError:
            pass
    assert hasattr(mod, name), f"{where}: `from {module} import {name}` — імені не існує"
