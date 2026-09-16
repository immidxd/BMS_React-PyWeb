"""Знімок локів парсера vs правка під час парсингу.

Парсер робить знімок залокованих полів на старті й відновлює їх у кінці.
Якщо між ними користувач (картка BMS / агент складу) змінив товар, у базі
вже новіші значення — відновлення зі знімка НЕ має їх відкочувати
(#Ф4419: 2100 → «повернулось» 2200).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.scripts import sheets_parser as sp  # noqa: E402


class _Prod:
    def __init__(self, id, price, edited_at, fields="price"):
        self.id, self.price, self.manually_edited_at, self.manually_edited_fields = id, price, edited_at, fields


class _Session:
    """Мінімальна сесія: get() з identity map + execute() для SQL, що використовує парсер."""
    def __init__(self, prods):
        self.prods = {p.id: p for p in prods}
        self.committed = False

    def get(self, _model, pid):
        return self.prods.get(pid)

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "SELECT id, manually_edited_fields" in sql:
            rows = [(p.id, p.manually_edited_fields) for p in self.prods.values()]
            return type("R", (), {"fetchall": lambda s: rows})()
        if "SELECT manually_edited_at" in sql:
            v = self.prods[params["pid"]].manually_edited_at
            return type("R", (), {"scalar": lambda s: v})()
        raise AssertionError(sql)

    def commit(self): self.committed = True
    def flush(self): pass


def test_restore_skips_rows_edited_during_parse(monkeypatch):
    monkeypatch.setattr(sp, "PRODUCT_LOCKS_ENABLED", True)
    t0 = datetime(2026, 9, 16, 20, 0, 0)
    a = _Prod(1, 2200.0, t0)          # редагували під час парсингу
    b = _Prod(2, 900.0, t0)           # не чіпали — парсер перезапише, треба відновити
    s = _Session([a, b])

    snap = sp._snapshot_product_locks(s)
    assert snap[1]["price"] == 2200.0 and snap[1]["__edited_at__"] == t0

    # ...парсер пише з аркуша: a — старе (2200 → 2200), b — старе (900 → 850)
    b.price = 850.0
    # ...користувач у цей час змінює a: 2200 → 2100 (лок оновлено)
    a.price, a.manually_edited_at = 2100.0, t0 + timedelta(seconds=7)

    restored = sp._restore_product_locks(s, snap)
    assert a.price == 2100.0, "правку під час парсингу відкотили до знімка"
    assert b.price == 900.0 and restored == 1 and s.committed


def test_restore_still_reverts_untouched_rows(monkeypatch):
    monkeypatch.setattr(sp, "PRODUCT_LOCKS_ENABLED", True)
    t0 = datetime(2026, 9, 16, 20, 0, 0)
    a = _Prod(1, 2200.0, t0)
    s = _Session([a])
    snap = sp._snapshot_product_locks(s)
    a.price = 1999.0                     # парсер перезаписав, manually_edited_at той самий
    assert sp._restore_product_locks(s, snap, commit=False) == 1
    assert a.price == 2200.0
