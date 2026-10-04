"""Зміна номера товару переносить фото (вони живуть за номером у назві файлу).

#Ф4503 → #Ф4510 (04.10.2026): номер змінився, а фото лишились під Ф4503 —
у картці 0 фото.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import photo_number_rename as pnr  # noqa: E402
import services.photo_manager as pm  # noqa: E402
import services.product_images as pi  # noqa: E402


class _DB:
    def __init__(self, siblings=0):
        self.siblings, self.sql, self.committed = siblings, [], False
    def execute(self, stmt, params=None):
        q = " ".join(str(stmt).split())
        self.sql.append((q, params))
        return types.SimpleNamespace(scalar=lambda: self.siblings)
    def commit(self): self.committed = True


def _img(name, cat="Взуття", hidden=False):
    return types.SimpleNamespace(filename=name, url=f"/product-images/{cat}/{name}?v=1", hidden=hidden)


def _patch(monkeypatch, images, fail=()):
    moves = []
    monkeypatch.setattr(pi, "list_images", lambda pn, include_hidden=False: images)
    monkeypatch.setattr(pi, "invalidate_hidden_cache", lambda: None)
    monkeypatch.setattr(pi, "invalidate_image_list_cache", lambda *a: None)
    monkeypatch.setattr(pi, "get_photo_pnum_set", lambda force=False: frozenset())

    def move(src, scat, fn, dst, dcat, to_kind=None):
        if fn in fail:
            raise RuntimeError("R2 недоступний")
        moves.append((src, scat, fn, dst, dcat))
        return {"moved": fn.replace("Ф4503", "Ф4510"), "target_pnum": "Ф4510"}
    monkeypatch.setattr(pm, "move_photo_to_product", move)
    return moves


def test_all_photos_follow_the_number_in_gallery_order(monkeypatch):
    moves = _patch(monkeypatch, [_img("Ф4503_001.webp"), _img("Ф4503_002.webp"), _img("Ф4503_def1.webp")])
    db = _DB()
    res = pnr.move_photos_to_new_number(db, "#Ф4503", "#Ф4510")
    assert res == {"moved": 3, "errors": []}
    assert [m[2] for m in moves] == ["Ф4503_001.webp", "Ф4503_002.webp", "Ф4503_def1.webp"]
    assert all(m[1] == m[4] == "Взуття" for m in moves)   # тека та сама
    assert db.committed


def test_hidden_mark_moves_with_the_file(monkeypatch):
    _patch(monkeypatch, [_img("Ф4503_001.webp", hidden=True)])
    db = _DB()
    pnr.move_photos_to_new_number(db, "#Ф4503", "#Ф4510")
    upd = [p for q, p in db.sql if q.startswith("UPDATE product_photo_hidden")]
    assert upd == [{"np": "Ф4510", "nf": "Ф4510_001.webp", "op": "Ф4503", "of": "Ф4503_001.webp"}]


def test_rostovka_sibling_keeps_shared_photos(monkeypatch):
    moves = _patch(monkeypatch, [_img("Ф4503_001.webp")])
    res = pnr.move_photos_to_new_number(_DB(siblings=1), "#Ф4503", "#Ф4510")
    assert res["moved"] == 0 and "ростовки" in res["skipped"] and moves == []


def test_one_failure_does_not_stop_the_rest(monkeypatch):
    moves = _patch(monkeypatch, [_img("Ф4503_001.webp"), _img("Ф4503_002.webp")], fail={"Ф4503_001.webp"})
    res = pnr.move_photos_to_new_number(_DB(), "#Ф4503", "#Ф4510")
    assert res["moved"] == 1 and "R2 недоступний" in res["errors"][0]
    assert [m[2] for m in moves] == ["Ф4503_002.webp"]
