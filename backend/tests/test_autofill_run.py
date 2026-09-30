"""Спільний одиничний прогін: картка й пакет мусять ходити ОДНИМ шляхом.

Раніше порядок шарів (живі знімки → шар виробника, якщо знімків немає) жив
у роутері. Пакет мусив би його повторити, а два однакові з вигляду шляхи
розходяться при першій правці — тест стереже, що копії не з'явилось.
"""
from __future__ import annotations

import sys
import types
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from services import autofill_run as ar


class _Db:
    def commit(self): pass
    def rollback(self): pass


def _product(number="#Ф4412", type_name="Ботинки"):
    return types.SimpleNamespace(productnumber=number,
                                 type=types.SimpleNamespace(typename=type_name))


def test_missing_product_is_an_answer_not_an_exception(monkeypatch):
    monkeypatch.setattr(ar.product_service, "get_product", lambda db, pid: None)
    res = ar.run_one(_Db(), 1)
    assert res["not_found"] is True and res["ok"] is False


def test_without_photos_falls_back_to_the_article_layer(monkeypatch):
    """Одна дія «Розпізнати» робить усе, що зараз можливо, а не відмовляє
    цілком через відсутність одного джерела."""
    monkeypatch.setattr(ar.product_service, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(ar, "resolve_category", lambda n, t: "Взуття")
    monkeypatch.setattr(ar, "_kind_files", lambda n, c, k: [])
    monkeypatch.setattr(ar.photo_autofill, "_current_values", lambda db, pid: {"marking": "X1"})
    monkeypatch.setattr(ar.web_enrich, "available", lambda cur: True)
    monkeypatch.setattr(ar.web_enrich, "enrich_by_article",
                        lambda db, pid: {"ok": True, "proposed": [("width", "G", 0.9)]})
    res = ar.run_one(_Db(), 7)
    assert res["ok"] and res["sources_used"] == ["артикул"]


def test_without_photos_and_without_article_says_what_to_do(monkeypatch):
    monkeypatch.setattr(ar.product_service, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(ar, "resolve_category", lambda n, t: "Взуття")
    monkeypatch.setattr(ar, "_kind_files", lambda n, c, k: [])
    monkeypatch.setattr(ar.photo_autofill, "_current_values", lambda db, pid: {})
    monkeypatch.setattr(ar.web_enrich, "available", lambda cur: False)
    res = ar.run_one(_Db(), 7)
    assert res["no_sources"] is True and "фото" in res["reason"]


def test_network_failure_becomes_a_readable_answer(monkeypatch):
    """Мережевий виняток — звичайна відмова зі своїм текстом, а не 500:
    голий 500 не лишає ані сліду в обліку витрат, ані причини людині."""
    monkeypatch.setattr(ar.product_service, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(ar, "resolve_category", lambda n, t: "Взуття")
    monkeypatch.setattr(ar, "_kind_files", lambda n, c, k: [pathlib.Path("/tmp/a.webp")])

    def boom(*a, **kw):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(ar.photo_autofill, "extract_and_propose", boom)
    res = ar.run_one(_Db(), 7)
    assert res["failed"] is True and "TimeoutError" in res["reason"]


def test_photo_ceiling_is_respected(monkeypatch):
    """Стеля кадрів на товар — запобіжник від півсотні фото, не економія
    квоти: Google рахує ЗАПИТИ, а не токени."""
    monkeypatch.setattr(ar.product_service, "get_product", lambda db, pid: _product())
    monkeypatch.setattr(ar, "resolve_category", lambda n, t: "Взуття")
    monkeypatch.setattr(ar, "_kind_files",
                        lambda n, c, k: [pathlib.Path(f"/tmp/{i}.webp") for i in range(50)])
    seen = {}
    monkeypatch.setattr(ar.photo_autofill, "extract_and_propose",
                        lambda db, pid, paths, **kw: seen.update(n=len(paths)) or {"ok": True})
    ar.run_one(_Db(), 7, photos=99)
    assert seen["n"] == ar.MAX_PHOTOS


def test_router_delegates_instead_of_duplicating():
    """Сторож проти другої копії логіки: роутер лише викликає run_one."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "routers" / "proposals.py").read_text("utf-8")
    body = src[src.index('def run_autofill('):src.index('def run_web_enrich(')]
    assert "autofill_run.run_one" in body
    for copied in ("_kind_files", "resolve_category", "extract_and_propose"):
        assert copied not in body, f"{copied} знову продубльовано в роутері"
