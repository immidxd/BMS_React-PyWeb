"""Приховані знімки: одна перевірка, і її бачать усі споживачі.

ЧОМУ ФІЛЬТР САМЕ В `list_images`. Через нього проходить УСЕ, що показує фото:
галерея картки, контент-план, Prom (а з ним OLX і Shafa) і Telegram. Поставити
перевірку в кожного споживача означало б завести ту саму дірку, що з мапами
write-back: ознака врахована в чотирьох місцях із восьми.

ЧОМУ ФАЙЛ НЕ ВИДАЛЯЄТЬСЯ. Уже опубліковані оголошення тримаються за URL у R2;
видалення показало б там биту картинку замість фото. Приховане просто перестає
пропонуватись будь-де надалі.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.services import product_images as pi  # noqa: E402


def _entry(name, kind="official", idx=0):
    return pi.ImageEntry(filename=name, url=f"/x/{name}", index=idx, kind=kind)


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """Жоден тест не ходить у справжню базу — набір задає сам тест."""
    monkeypatch.setattr(pi, "_hidden_keys", lambda force=False: frozenset())


def _with_hidden(monkeypatch, *keys):
    monkeypatch.setattr(pi, "_hidden_keys", lambda force=False: frozenset(keys))


# ── Відсів ──────────────────────────────────────────────────────────────────

def test_hidden_is_dropped_for_everyone_else(monkeypatch):
    _with_hidden(monkeypatch, ("ф4384", "ф4384_02.webp"))
    entries = [_entry("Ф4384_01.webp", idx=0), _entry("Ф4384_02.webp", idx=1),
               _entry("Ф4384_03.webp", idx=2)]
    out = pi._apply_hidden("Ф4384", entries, include_hidden=False)
    assert [e.filename for e in out] == ["Ф4384_01.webp", "Ф4384_03.webp"]


def test_card_sees_hidden_but_marked(monkeypatch):
    _with_hidden(monkeypatch, ("ф4384", "ф4384_02.webp"))
    entries = [_entry("Ф4384_01.webp", idx=0), _entry("Ф4384_02.webp", idx=1)]
    out = pi._apply_hidden("Ф4384", entries, include_hidden=True)
    assert len(out) == 2
    assert [e.hidden for e in out] == [False, True]


def test_indexes_are_recomputed_after_the_cut(monkeypatch):
    """Сховане ГОЛОВНЕ не має лишати діру: споживачі беруть перше за index=0.

    Без переіндексації головним лишився б порожній слот, і пост пішов би без
    обкладинки або з чужим кадром.
    """
    _with_hidden(monkeypatch, ("ф4384", "ф4384_01.webp"))
    entries = [_entry("Ф4384_01.webp", idx=0), _entry("Ф4384_02.webp", idx=1),
               _entry("Ф4384_03.webp", idx=2)]
    out = pi._apply_hidden("Ф4384", entries, include_hidden=False)
    assert [(e.filename, e.index) for e in out] == [
        ("Ф4384_02.webp", 0), ("Ф4384_03.webp", 1)]


def test_hiding_one_photo_does_not_touch_another_product(monkeypatch):
    _with_hidden(monkeypatch, ("ф4384", "ф4384_01.webp"))
    out = pi._apply_hidden("Ф4385", [_entry("Ф4385_01.webp")], include_hidden=False)
    assert len(out) == 1 and out[0].hidden is False


# ── Складання регістру ──────────────────────────────────────────────────────

def test_case_folding_is_done_in_python_not_postgres():
    """Пастка, що вже спрацювала: у локалі C Postgres не опускає кирилицю.

    `lower('Ф4384')` у базі повертає 'Ф4384', а Python дає 'ф4384' — ключі
    ніколи не збігались, і приховування мовчки не діяло. Тому регістр
    складається в Python з обох боків.
    """
    # ⚠️ Читаємо ФАЙЛ, а не inspect.getsource: autouse-фікстура вище вже
    # підмінила `_hidden_keys` лямбдою, і getsource повернув би саме її.
    src = (BACKEND / "services" / "product_images.py").read_text(encoding="utf-8")
    assert "SELECT productnumber, filename FROM product_photo_hidden" in src, \
        "знову опускаємо регістр у SQL — у локалі C це не працює для кирилиці"
    assert 'lower(productnumber)' not in src


@pytest.mark.parametrize("stored, looked_up", [
    ("Ф4384_01.webp", "ф4384_01.WEBP"),
    ("Ф4384_01.webp", "Ф4384_01.webp"),
])
def test_lookup_ignores_case(monkeypatch, stored, looked_up):
    _with_hidden(monkeypatch, ("ф4384", stored.lower()))
    out = pi._apply_hidden("Ф4384", [_entry(looked_up)], include_hidden=False)
    assert out == []


# ── Стійкість ───────────────────────────────────────────────────────────────

def test_db_failure_hides_nothing(monkeypatch):
    """Збій бази НЕ має ховати всі фото — це зробило б аварію видимою покупцям."""
    monkeypatch.setattr(pi, "_HIDDEN_CACHE", {"at": 0.0, "keys": frozenset()})
    def _boom(*a, **k):
        raise RuntimeError("база лягла")
    monkeypatch.setattr("backend.models.database.SessionLocal", _boom, raising=False)
    monkeypatch.setattr("models.database.SessionLocal", _boom, raising=False)
    assert pi._hidden_keys(force=True) == frozenset()


# ── Контракт зі споживачами ─────────────────────────────────────────────────

def test_default_excludes_hidden():
    """Типове значення — САМЕ False. Якби типовим було True, кожен новий
    споживач мовчки показував би приховане."""
    import inspect
    sig = inspect.signature(pi.list_images)
    assert sig.parameters["include_hidden"].default is False


def test_only_the_card_asks_for_hidden():
    """`include_hidden=True` дозволений рівно в одному місці — галереї картки."""
    router = (BACKEND / "routers" / "products.py").read_text(encoding="utf-8")
    # Рахуємо ВИКЛИКИ, а не згадки: поряд стоїть пояснювальний коментар, і
    # проста підстрічка зарахувала б і його.
    calls = [ln for ln in router.splitlines()
             if "include_hidden=True" in ln and "list_images(" in ln]
    assert len(calls) == 2, (
        "include_hidden=True має бути лише у _product_gallery "
        f"(власні фото + позичені в донора), знайдено: {calls}")
    for name in ("prom_service", "telegram_publisher"):
        src = (BACKEND / "services" / f"{name}.py").read_text(encoding="utf-8")
        assert "include_hidden" not in src, f"{name} обходить фільтр"


def test_entry_carries_the_flag():
    assert "hidden" in pi.ImageEntry.__dataclass_fields__
    assert pi.ImageEntry.__dataclass_fields__["hidden"].default is False
