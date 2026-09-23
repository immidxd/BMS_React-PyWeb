"""Прийнята пропозиція мусить доїхати в Журнал, а не лише в базу.

ДІРА, ЯКУ ЦЕ ЗАКРИВАЄ. `update_product` сам у чергу write-back не пише — лише
позначає поля на обʼєкті. Читав цю розмітку ТІЛЬКИ роутер PUT /api/products,
а роутер пропозицій викликав update_product напряму. Наслідок: прийняте
лочилось у базі, а Журнал його не отримував — #Ф2084, пʼять полів, у черзі
нуль. І лок далі не давав парсеру вирівняти це з боку аркуша.

Тепер черга ставиться ОДНІЄЮ функцією сервісу, і обидва роутери її викликають.
"""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.services import product_service as ps  # noqa: E402


class _JS:
    def __init__(self): self.calls = []; self.kicked = 0
    def enqueue_many(self, db, pid, pnum, sheet, values): self.calls.append((pid, pnum, sheet, dict(values)))
    def kick(self): self.kicked += 1


class _DB:
    def __init__(self): self.commits = 0
    def commit(self): self.commits += 1


def _patch(monkeypatch, js):
    import types
    fake = types.ModuleType("journal_sync"); fake.enqueue_many = js.enqueue_many; fake.kick = js.kick
    monkeypatch.setitem(sys.modules, "services.journal_sync", fake)
    monkeypatch.setitem(sys.modules, "backend.services.journal_sync", fake)
    # `from services import journal_sync` бере АТРИБУТ пакета, якщо справжній
    # модуль уже імпортував інший тест, — sys.modules тоді не читається.
    for pkg in ("services", "backend.services"):
        if pkg in sys.modules:
            monkeypatch.setattr(sys.modules[pkg], "journal_sync", fake, raising=False)
    monkeypatch.setattr(ps, "get_delivery_name", lambda db, did: "05.09.2026(Соня)")
    monkeypatch.setattr(ps, "resolve_lookup_name", lambda db, f, v: f"назва({v})")


def test_locked_fields_reach_the_queue(monkeypatch):
    js = _JS(); _patch(monkeypatch, js); db = _DB()
    prod = SimpleNamespace(id=7, productnumber="#Ф2084", deliveryid=3,
                           soletypeid=11, marking="X1",
                           _writeback_fields={"soletypeid", "marking"})
    out = ps.enqueue_writeback_for(db, prod)
    assert len(js.calls) == 1
    pid, pnum, sheet, values = js.calls[0]
    assert (pid, pnum, sheet) == (7, "#Ф2084", "05.09.2026(Соня)")
    # FK-довідник їде НАЗВОЮ, не id
    assert values == {"soletypeid": "назва(11)", "marking": "X1"} == out
    assert db.commits == 1 and js.kicked == 1


def test_nothing_to_write_means_nothing_queued(monkeypatch):
    js = _JS(); _patch(monkeypatch, js); db = _DB()
    assert ps.enqueue_writeback_for(db, SimpleNamespace(id=1, productnumber="#А1", deliveryid=1)) == {}
    assert js.calls == [] and db.commits == 0


def test_materials_measurements_technologies_are_synthetic_fields(monkeypatch):
    js = _JS(); _patch(monkeypatch, js)
    prod = SimpleNamespace(id=2, productnumber="#А2", deliveryid=1,
                           _material_writeback={"верх": "шкіра, текстиль"},
                           _measurement_writeback={"meas_length": "26-27"},
                           _technology_writeback="Gore-Tex")
    values = ps.enqueue_writeback_for(_DB(), prod)
    assert values == {"material_верх": "шкіра, текстиль", "meas_length": "26-27",
                      "technologyid": "Gore-Tex"}


# ── Обидва шляхи в картку ходять через одну функцію ─────────────────────────

def test_both_routers_delegate_and_neither_enqueues_itself():
    products = (BACKEND / "routers" / "products.py").read_text(encoding="utf-8")
    proposals = (BACKEND / "routers" / "proposals.py").read_text(encoding="utf-8")
    assert "product_service.enqueue_writeback_for(db, updated_product)" in products
    assert "product_service.enqueue_writeback_for(db, updated)" in proposals
    # жоден роутер не має власної копії логіки черги
    assert "enqueue_many(" not in products
    assert "enqueue_many(" not in proposals
