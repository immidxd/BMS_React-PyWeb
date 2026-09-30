"""Обидва «прийняти» мусять повертати `applied` — від цього залежить картка.

Режим редагування тримає ВЛАСНУ копію значень (`drafts`), зняту при вході.
Тому після прийняття картка освіжає саме ті чернетки, яких торкнувся сервер,
і дізнається про них лише з `applied`. Без цього поля прийняте значення
лягало б у базу, а на екрані лишалось старе — і наступне «Зберегти все»
мовчки записало б старе назад (#Ф4413, протектор «гладка»).
"""
from __future__ import annotations

import ast
import pathlib

ROUTER = pathlib.Path(__file__).resolve().parents[1] / "routers" / "proposals.py"


def _returned_keys(fn_name: str) -> set[str]:
    tree = ast.parse(ROUTER.read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name)
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def test_single_accept_tells_the_card_what_it_applied():
    assert "applied" in _returned_keys("accept_proposal")


def test_accept_all_tells_the_card_what_it_applied():
    assert "applied" in _returned_keys("accept_all_proposals")
