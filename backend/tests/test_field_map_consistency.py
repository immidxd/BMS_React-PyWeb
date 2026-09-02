"""Кожне поле товару має бути в УСІХ мапах, а не в частині з них.

Поле живе приблизно у двадцяти місцях (див. пам'ять проєкту). Найтиповіший
спосіб зробити дірку — дописати його в дві мапи з п'яти. Симптом тихий: у
програмі поле працює, а після проходу парсера зникає або їде в аркуш сирим id.

Тест читає мапи СТАТИЧНО через ast — без імпорту модулів і без БД. Так він:
  • не залежить від gspread/psycopg2 і від того, чи піднятий сервер;
  • не спотикається об пастку подвійного імпорту (services.X vs backend.services.X);
  • не має побічних ефектів (імпорт sheets_parser тягне мережеві клієнти).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
PARSER = _BACKEND / "scripts" / "sheets_parser.py"
SERVICE = _BACKEND / "services" / "product_service.py"


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_strings(node: ast.AST) -> set[str]:
    """Рядкові літерали з set/list/tuple; для dict — КЛЮЧІ."""
    if isinstance(node, ast.Dict):
        items = node.keys
    elif isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        items = node.elts
    else:
        return set()
    return {e.value for e in items
            if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _module_collection(tree: ast.Module, name: str) -> set[str]:
    """Верхньорівнева колекція за іменем → множина рядкових елементів/ключів."""
    for node in tree.body:
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                return _literal_strings(node.value)
    raise AssertionError(f"{name} не знайдено на верхньому рівні — мапу перейменували?")


def _dict_values_first(tree: ast.Module, name: str) -> set[str]:
    """Для мап виду {name: (fk, table, col)} — перший елемент кортежу-значення."""
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and isinstance(node.value, ast.Dict):
                out = set()
                for v in node.value.values:
                    if isinstance(v, ast.Tuple) and v.elts:
                        first = v.elts[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            out.add(first.value)
                return out
    raise AssertionError(f"{name} не знайдено або це не dict")


def _keys_of_dict_in_function(tree: ast.Module, func: str) -> set[str]:
    """Ключі найбільшого dict-літерала всередині функції (мапа fk → таблиця)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            dicts = [n for n in ast.walk(node) if isinstance(n, ast.Dict) and n.keys]
            if not dicts:
                raise AssertionError(f"у {func} немає dict-літерала")
            return _literal_strings(max(dicts, key=lambda d: len(d.keys)))
    raise AssertionError(f"функцію {func} не знайдено")


@pytest.fixture(scope="module")
def maps() -> dict[str, set[str]]:
    p, s = _tree(PARSER), _tree(SERVICE)
    return {
        # ── парсер / Журнал
        "PRODUCT_LOCK_FIELDS":       _module_collection(p, "PRODUCT_LOCK_FIELDS"),
        "WRITEBACK_FIELD_HEADERS":   _module_collection(p, "WRITEBACK_FIELD_HEADERS"),
        "PER_ITEM_WRITEBACK_FIELDS": _module_collection(p, "PER_ITEM_WRITEBACK_FIELDS"),
        "_NEW_SINGLE_FK_FIELDS":     _module_collection(p, "_NEW_SINGLE_FK_FIELDS"),
        # ── сервіс товару
        "LOCKABLE_PRODUCT_FIELDS":   _module_collection(s, "LOCKABLE_PRODUCT_FIELDS"),
        "SHOE_FK_NAME_FIELDS":       _module_collection(s, "SHOE_FK_NAME_FIELDS"),
        "PER_ITEM_FIELDS":           _module_collection(s, "PER_ITEM_FIELDS"),
        "LOOKUP_NAME_FIELDS_fks":    _dict_values_first(s, "LOOKUP_NAME_FIELDS"),
        "resolve_lookup_name_keys":  _keys_of_dict_in_function(s, "resolve_lookup_name"),
    }


def test_maps_are_found(maps):
    """Санітарна перевірка: жодна мапа не порожня (інакше решта тестів фіктивна)."""
    for name, values in maps.items():
        assert values, f"{name} порожня — розбір зламався, решта перевірок недійсна"


def test_fk_written_back_as_name_is_resolvable(maps):
    """FK, що пишеться в аркуш назвою, мусить мати запис у resolve_lookup_name.

    Без нього writeback покладе в Журнал СИРИЙ id («3» замість «Китай»), парсер
    прочитає його назад як назву й розплодить довідник-привид з іменами-числами.
    Саме так це вже одного разу сталося з manufacturercountryid.
    """
    missing = maps["SHOE_FK_NAME_FIELDS"] - maps["resolve_lookup_name_keys"]
    assert not missing, (
        "FK пишеться назвою, але resolve_lookup_name його не знає → у Журнал "
        f"поїде сирий id: {sorted(missing)}"
    )


def test_inline_edited_fk_is_lockable(maps):
    """Кожен FK, редагований назвою в картці, мусить бути в LOCKABLE_PRODUCT_FIELDS.

    Інакше правка не залочиться і найближчий прохід парсера її затре.
    """
    missing = maps["LOOKUP_NAME_FIELDS_fks"] - maps["LOCKABLE_PRODUCT_FIELDS"]
    assert not missing, (
        f"FK редагується в картці, але не залочується — парсер затре: {sorted(missing)}"
    )


def test_lockable_field_has_journal_column(maps):
    """Залочуване поле мусить мати колонку Журналу.

    Без неї writeback_field_to_journal повертає «no journal column for '<field>'»,
    і правка лишається лише в БД: аркуш відстає назавжди й мовчки.
    """
    missing = maps["LOCKABLE_PRODUCT_FIELDS"] - set(maps["WRITEBACK_FIELD_HEADERS"])
    assert not missing, (
        f"поле залочується, але в Журнал потрапити не може: {sorted(missing)}"
    )


def test_app_locks_are_honored_by_parser(maps):
    """Те, що додаток лочить, парсер мусить поважати.

    LOCKABLE_PRODUCT_FIELDS (сервіс) ⊆ PRODUCT_LOCK_FIELDS (парсер). Поле поза
    списком парсера буде відновлене з аркуша попри ручну правку користувача.
    """
    missing = maps["LOCKABLE_PRODUCT_FIELDS"] - maps["PRODUCT_LOCK_FIELDS"]
    assert not missing, (
        f"додаток лочить поле, а парсер про лок не знає й затре його: {sorted(missing)}"
    )


def test_new_single_fk_fields_are_known_everywhere(maps):
    """Взуттєві FK з парсера мусять бути відомі сервісу — і як FK-назва, і як лок."""
    fks = maps["_NEW_SINGLE_FK_FIELDS"]
    assert not (fks - maps["SHOE_FK_NAME_FIELDS"]), (
        f"парсер знає FK, сервіс не пише його назвою: {sorted(fks - maps['SHOE_FK_NAME_FIELDS'])}")
    assert not (fks - maps["LOCKABLE_PRODUCT_FIELDS"]), (
        f"парсер знає FK, сервіс його не лочить: {sorted(fks - maps['LOCKABLE_PRODUCT_FIELDS'])}")


def test_per_item_writeback_fields_have_columns(maps):
    """Per-item поля теж пишуться в аркуш — колонка обов'язкова."""
    missing = maps["PER_ITEM_WRITEBACK_FIELDS"] - set(maps["WRITEBACK_FIELD_HEADERS"])
    assert not missing, f"per-item поле без колонки Журналу: {sorted(missing)}"


def test_per_item_definitions_agree(maps):
    """Списки per-item у парсері й сервісі не мають суперечити один одному.

    Поле, per-item лише з одного боку, або затре сусідні розміри ростовки,
    або не пошириться туди, куди мало.
    """
    parser_side = {f for f in maps["PER_ITEM_WRITEBACK_FIELDS"] if not f.startswith("meas_")}
    service_side = maps["PER_ITEM_FIELDS"]
    # conditionid — суто внутрішній для сервісу (в аркуш іде current_conditionid)
    disagreement = parser_side.symmetric_difference(service_side) - {"conditionid"}
    assert not disagreement, (
        f"парсер і сервіс не згодні, які поля per-item: {sorted(disagreement)}"
    )
