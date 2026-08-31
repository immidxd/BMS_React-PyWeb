"""Перевішування посилань на товар — гарди, що стоять між злиттям і втратою даних.

До 01.09.2026 кнопка «прийняти» в картці кандидатів перевішувала рівно дві
таблиці (`order_items`, `telegram_posts`) і видаляла товар. Решта 20 місць
лишались: три без FK висіли на неіснуючому id, публікації обнулялись через
ON DELETE SET NULL, а `order_details` (FK без дії) поклав би merge у 500.
Тепер обидва шляхи злиття ходять через `services/product_refs`.
"""
import re

import pytest
from sqlalchemy.exc import IntegrityError

from backend.services.product_refs import repoint_product_refs


class _Fake:
    """Крихітна БД у пам'яті: {таблиця: [рядок, …]}, ctid = індекс рядка.

    Розуміє рівно ті форми запитів, які надсилає `repoint_product_refs`.
    `raise_on` дозволяє зімітувати порушення унікального ключа при UPDATE.
    """

    def __init__(self, tables, raise_on=()):
        self.tables = {t: list(rows) for t, rows in tables.items()}
        self.raise_on = set(raise_on)          # {(таблиця, ctid)}
        self.deleted, self.updated = [], []

    # -- інтерфейс, який використовує хелпер ---------------------------------
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        params = params or {}

        m = re.match(r"SELECT ctid FROM (\w+) WHERE (\w+) = :i$", sql)
        if m:
            tbl, col = m.group(1), m.group(2)
            return _Res([(i,) for i, r in enumerate(self.tables.get(tbl, []))
                         if r is not None and r.get(col) == params["i"]])

        m = re.match(r"SELECT count\(\*\) FROM (\w+) WHERE (\w+) = :i$", sql)
        if m:
            tbl, col = m.group(1), m.group(2)
            n = sum(1 for r in self.tables.get(tbl, [])
                    if r is not None and r.get(col) == params["i"])
            return _Res([(n,)])

        m = re.match(r"SELECT (.+) FROM (\w+) WHERE ctid = :c$", sql)
        if m:
            cols = [c.strip() for c in m.group(1).split(",")]
            row = self.tables[m.group(2)][params["c"]]
            return _Res([tuple(row.get(c) for c in cols)])

        m = re.match(r"UPDATE (\w+) SET (\w+) = :t WHERE ctid = :c$", sql)
        if m:
            tbl, col, ctid = m.group(1), m.group(2), params["c"]
            if (tbl, ctid) in self.raise_on:
                raise IntegrityError("unique", {}, Exception("duplicate key"))
            self.tables[tbl][ctid][col] = params["t"]
            self.updated.append((tbl, col, ctid))
            return _Res([])

        m = re.match(r"DELETE FROM (\w+) WHERE ctid = :c$", sql)
        if m:
            self.tables[m.group(1)][params["c"]] = None
            self.deleted.append((m.group(1), params["c"]))
            return _Res([])

        raise AssertionError(f"несподіваний запит: {sql}")

    def begin_nested(self):
        fake = self

        class _SP:
            def commit(self): pass
            def rollback(self):
                fake.rolled_back = True
        return _SP()


class _Res:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None
    def scalar(self): return self._rows[0][0] if self._rows else None


REFS = [("order_items", "product_id"), ("product_images", "productid"),
        ("merge_candidates", "new_product_id"), ("merge_candidates", "suggested_id")]


def test_every_reference_moves_not_just_the_two_with_fk():
    db = _Fake({
        "order_items":   [{"product_id": 10}],
        "product_images": [{"productid": 10}],       # ← без FK, раніше висіло б
        "merge_candidates": [],
    })
    res = repoint_product_refs(db, 10, 20, refs=REFS)
    assert db.tables["order_items"][0]["product_id"] == 20
    assert db.tables["product_images"][0]["productid"] == 20
    assert res["moved"]["product_images.productid"] == 1


def test_row_that_would_point_at_itself_is_deleted():
    # Рядок кандидата тримає ОБИДВА кінці пари: перевісити new на suggested
    # означало б «товар — двійник самого себе».
    db = _Fake({
        "order_items": [], "product_images": [],
        "merge_candidates": [{"new_product_id": 10, "suggested_id": 20}],
    })
    res = repoint_product_refs(db, 10, 20, refs=REFS)
    assert db.tables["merge_candidates"][0] is None
    assert res["dropped_as_duplicate"]["merge_candidates.new_product_id"] == 1


def test_unrelated_candidate_pair_is_kept_and_moved():
    # (10 → 33) не самопосилання: після злиття пропозиція стосується вцілілого.
    db = _Fake({
        "order_items": [], "product_images": [],
        "merge_candidates": [{"new_product_id": 10, "suggested_id": 33}],
    })
    repoint_product_refs(db, 10, 20, refs=REFS)
    assert db.tables["merge_candidates"][0] == {"new_product_id": 20, "suggested_id": 33}


def test_duplicate_on_the_new_owner_is_dropped_not_fatal():
    # Такий самий рядок у вцілілого вже є → унікальний ключ. Це не привід
    # валити злиття: потрібного стану вже досягнуто.
    db = _Fake({
        "order_items": [{"product_id": 10}], "product_images": [],
        "merge_candidates": [],
    }, raise_on={("order_items", 0)})
    res = repoint_product_refs(db, 10, 20, refs=REFS)
    assert db.tables["order_items"][0] is None
    assert res["dropped_as_duplicate"]["order_items.product_id"] == 1


def test_skip_tables_leaves_those_references_alone():
    db = _Fake({
        "order_items": [{"product_id": 10}], "product_images": [],
        "merge_candidates": [{"new_product_id": 10, "suggested_id": 33}],
    })
    repoint_product_refs(db, 10, 20, refs=REFS, skip_tables={"merge_candidates"})
    assert db.tables["order_items"][0]["product_id"] == 20
    assert db.tables["merge_candidates"][0]["new_product_id"] == 10   # каскаду


def test_a_reference_left_behind_raises_instead_of_going_quiet():
    """ctid — фізичний вказівник: паралельний запис із запущеного застосунку
    зрушив би рядок, UPDATE зачепив би нуль рядків, і посилання лишилось би
    висіти БЕЗ жодної помилки. Тому наприкінці перевіряється факт."""
    class _Sticky(_Fake):
        def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            if sql.startswith("UPDATE"):
                return _Res([])                 # вдаємо, що UPDATE не спрацював
            return super().execute(stmt, params)

    db = _Sticky({"order_items": [{"product_id": 10}], "product_images": [],
                  "merge_candidates": []})
    with pytest.raises(RuntimeError, match="лишились посилання"):
        repoint_product_refs(db, 10, 20, refs=REFS)


def test_repointing_onto_itself_is_refused():
    with pytest.raises(ValueError):
        repoint_product_refs(_Fake({}), 10, 10, refs=REFS)
