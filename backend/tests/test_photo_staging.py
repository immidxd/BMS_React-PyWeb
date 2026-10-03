"""Розкладання фото з теки «до розбору»: безпека шляхів, пошук товару, _done."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.routers import photo_staging as ps  # noqa: E402


@pytest.fixture
def staging(tmp_path, monkeypatch):
    root = tmp_path / "до_розбору"
    (root / "Взуття").mkdir(parents=True)
    monkeypatch.setattr(ps, "STAGING_ROOT", root)
    # Кеш мініатюр — у тимчасовій теці, а не в ~/.cache користувача; фоновий
    # прогрів вимкнено, щоб потік не жив довше за tmp_path.
    monkeypatch.setattr(ps, "_THUMB_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(ps, "_PREWARM_ENABLED", False)
    from PIL import Image
    for n in ("a.jpg", "b.jpg", "c.png"):
        Image.new("RGB", (400, 400), "gray").save(root / "Взуття" / n)
    (root / "Взуття" / "notes.txt").write_text("x")
    return root


# ── Безпека шляхів ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../secret.jpg", "sub/a.jpg", ".hidden.jpg", "", "a\\b.jpg"])
def test_path_traversal_is_refused(staging, bad):
    with pytest.raises(HTTPException) as e:
        ps._safe_path("Взуття", bad)
    assert e.value.status_code in (400, 404)


def test_unknown_category_is_refused(staging):
    with pytest.raises(HTTPException) as e:
        ps._safe_path("../../etc", "a.jpg")
    assert e.value.status_code == 400


def test_non_images_are_not_listed(staging):
    names = [f["name"] for f in ps.staging_list(category="Взуття")["files"]]
    assert set(names) == {"a.jpg", "b.jpg", "c.png"}


def test_thumbnail_is_jpeg_and_bounded(staging):
    r = ps.staging_image(category="Взуття", name="a.jpg", w=100)
    assert r.media_type == "image/jpeg" and 0 < len(r.body) < 20_000


# ── Пошук товару ────────────────────────────────────────────────────────────

class _Q:
    def __init__(self, rows): self.rows = rows
    def filter(self, *a): return self
    def order_by(self, *a): return self
    def all(self): return self.rows


def test_product_is_found_with_or_without_hash():
    prod = SimpleNamespace(id=1, productnumber="#Ф4400", type=None)
    db = SimpleNamespace(query=lambda m: _Q([prod]))
    assert ps._find_product(db, "Ф4400") is prod
    assert ps._find_product(db, "#Ф4400") is prod


def test_missing_product_tells_to_add_it_first():
    db = SimpleNamespace(query=lambda m: _Q([]))
    with pytest.raises(HTTPException) as e:
        ps._find_product(db, "Ф9999")
    assert e.value.status_code == 404 and "додай" in e.value.detail


# ── Прикріплення ────────────────────────────────────────────────────────────

def test_attach_goes_through_add_photos_and_moves_originals_to_done(staging, monkeypatch):
    """Той самий шлях, що й кнопка «Додати» в картці; оригінали — в _done, не в кошик."""
    calls = {}
    monkeypatch.setattr(ps, "add_photos", lambda pnum, cat, sources, kind: calls.update(
        pnum=pnum, cat=cat, n=len(sources), kind=kind) or {"added": len(sources), "errors": []})
    monkeypatch.setattr(ps, "resolve_category", lambda pnum, t: "Взуття")
    monkeypatch.setattr(ps, "invalidate_image_list_cache", lambda *a: None)
    import types
    fake = types.ModuleType("routers.products"); fake._invalidate_photo_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "routers.products", fake)
    monkeypatch.setitem(sys.modules, "backend.routers.products", fake)
    prod = SimpleNamespace(id=7, productnumber="#Ф4400", type=SimpleNamespace(typename="Кросівки"))
    db = SimpleNamespace(query=lambda m: _Q([prod]))

    out = ps.staging_attach({"category": "Взуття", "productnumber": "Ф4400",
                             "files": ["a.jpg", "b.jpg"], "kind": "real"}, db=db)
    assert out["ok"] and out["added"] == 2 and out["moved"] == 2
    assert calls == {"pnum": "#Ф4400", "cat": "Взуття", "n": 2, "kind": "real"}
    done = staging / "Взуття" / "_done" / "Ф4400"
    assert sorted(p.name for p in done.iterdir()) == ["a.jpg", "b.jpg"]
    assert not (staging / "Взуття" / "a.jpg").exists()
    assert (staging / "Взуття" / "c.png").exists(), "невибране лишилось у теці"


def test_failed_file_stays_in_staging(staging, monkeypatch):
    """Битий файл не переноситься в _done — людина має його побачити знову."""
    monkeypatch.setattr(ps, "add_photos", lambda pnum, cat, sources, kind: {
        "added": 1, "errors": [{"file": "b.jpg", "reason": "битий"}]})
    monkeypatch.setattr(ps, "resolve_category", lambda pnum, t: "Взуття")
    monkeypatch.setattr(ps, "invalidate_image_list_cache", lambda *a: None)
    import types
    fake = types.ModuleType("routers.products"); fake._invalidate_photo_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "routers.products", fake)
    monkeypatch.setitem(sys.modules, "backend.routers.products", fake)
    prod = SimpleNamespace(id=7, productnumber="#Ф4400", type=None)
    db = SimpleNamespace(query=lambda m: _Q([prod]))
    out = ps.staging_attach({"category": "Взуття", "productnumber": "Ф4400",
                             "files": ["a.jpg", "b.jpg"]}, db=db)
    assert out["moved"] == 1
    assert (staging / "Взуття" / "b.jpg").exists()


def test_attach_refuses_unknown_kind_and_empty_selection(staging):
    db = SimpleNamespace(query=lambda m: _Q([]))
    with pytest.raises(HTTPException): ps.staging_attach({"category": "Взуття", "productnumber": "x", "files": ["a.jpg"], "kind": "xxx"}, db=db)
    with pytest.raises(HTTPException): ps.staging_attach({"category": "Взуття", "productnumber": "x", "files": []}, db=db)


def test_counts_tell_which_cards_already_have_photos(monkeypatch):
    """Власник плутався й підвʼязував повторно: чіпи завозу показували лише
    роздане ЗА СЕСІЮ. Тепер — скільки в картці є насправді."""
    from types import SimpleNamespace
    from backend.routers import photo_staging as ps
    def _fake(n):
        return {"#Ф1": [SimpleNamespace(kind="real"), SimpleNamespace(kind="real"), SimpleNamespace(kind="official")],
                "#Ф2": []}.get(n, [])
    monkeypatch.setattr(ps, "list_images", _fake)
    out = ps.staging_counts(numbers="#Ф1, #Ф2,,#Ф3")
    assert out["counts"]["#Ф1"] == {"real": 2, "official": 1, "defect": 0}
    assert out["counts"]["#Ф2"] == {"real": 0, "official": 0, "defect": 0}
    assert out["counts"]["#Ф3"]["real"] == 0


def test_delete_moves_to_trash_not_unlink(staging):
    """«Повністю видалити» з розбору = у _trash/ тієї ж категорії: оригінали
    єдині, і клік не туди в сітці з 60 кадрів не має коштувати знімка."""
    out = ps.staging_delete({"category": "Взуття", "files": ["a.jpg", "b.jpg"]})
    assert out["ok"] and out["deleted"] == 2 and sorted(out["files"]) == ["a.jpg", "b.jpg"]
    assert not (staging / "Взуття" / "a.jpg").exists()
    assert (staging / "Взуття" / "_trash" / "a.jpg").exists() and (staging / "Взуття" / "_trash" / "b.jpg").exists()
    # зі списку зникли, а _trash не показується як категорія-вміст
    assert [f["name"] for f in ps.staging_list("Взуття")["files"]] == ["c.png"]


def test_delete_refuses_paths_and_empty_selection(staging):
    import pytest
    with pytest.raises(Exception):
        ps.staging_delete({"category": "Взуття", "files": ["../a.jpg"]})
    with pytest.raises(Exception):
        ps.staging_delete({"category": "Взуття", "files": []})
    assert (staging / "Взуття" / "a.jpg").exists()


def test_delete_keeps_a_name_collision_in_trash(staging):
    ps.staging_delete({"category": "Взуття", "files": ["a.jpg"]})
    from PIL import Image
    Image.new("RGB", (10, 10), "red").save(staging / "Взуття" / "a.jpg")   # новий файл з тією ж назвою
    ps.staging_delete({"category": "Взуття", "files": ["a.jpg"]})
    names = sorted(p.name for p in (staging / "Взуття" / "_trash").iterdir())
    assert names == ["a.jpg", "a_1.jpg"]


def test_restore_brings_a_file_back_from_trash(staging):
    """«Повернути» в тості після ×: файл повертається з _trash у розбір."""
    ps.staging_delete({"category": "Взуття", "files": ["a.jpg"]})
    out = ps.staging_restore({"category": "Взуття", "files": ["a.jpg", "ghost.jpg", "../x.jpg"]})
    assert out["restored"] == ["a.jpg"] and len(out["errors"]) == 2
    assert (staging / "Взуття" / "a.jpg").exists() and not (staging / "Взуття" / "_trash" / "a.jpg").exists()


# ── Швидкість сітки: мініатюри й сортування ─────────────────────────────────

def test_list_carries_added_time_for_sorting(staging):
    """Сортування «нові спершу» — за часом, коли файл ЛІГ у теку (ctime)."""
    files = ps.staging_list("Взуття")["files"]
    assert files and all(isinstance(f["added"], int) and f["added"] > 0 for f in files)


def test_thumbnail_is_cached_on_disk_and_survives_memory(staging, monkeypatch):
    """Друге звернення не декодує оригінал — читає готовий кадр із диска."""
    calls = []
    real = ps._render_thumb
    monkeypatch.setattr(ps, "_render_thumb", lambda p, w: calls.append(w) or real(p, w))
    a = ps.staging_image(category="Взуття", name="a.jpg", w=400, v="1").body
    b = ps.staging_image(category="Взуття", name="a.jpg", w=400, v="1").body
    assert a == b and calls == [400]
    assert list((staging.parent / "thumbs" / "400").rglob("*.jpg"))


def test_thumbnail_cache_follows_file_changes(staging):
    """Заміна файлу під тією ж назвою — новий ключ кешу, а не старий кадр."""
    from PIL import Image
    p = staging / "Взуття" / "a.jpg"
    before = ps._thumb_cache_path(p, 400)
    import os, time
    Image.new("RGB", (300, 500), "white").save(p)
    os.utime(p, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    assert ps._thumb_cache_path(p, 400) != before


def test_grid_thumb_covers_and_preview_contains(staging):
    """Плитка: МЕНША сторона = 400 (квадрат без розмиття на retina);
    превʼю: БІЛЬША сторона ≤ 1200. Довільна ширина — до найближчої дозволеної."""
    from PIL import Image
    import io
    Image.new("RGB", (3000, 1500), "gray").save(staging / "Взуття" / "wide.jpg")
    grid = Image.open(io.BytesIO(ps.staging_image(category="Взуття", name="wide.jpg", w=220).body))
    prev = Image.open(io.BytesIO(ps.staging_image(category="Взуття", name="wide.jpg", w=1200).body))
    assert grid.size == (800, 400)
    assert prev.size == (1200, 600)
    assert ps._norm_width(220) == 400 and ps._norm_width(999) == 1200 and ps._norm_width(5000) == 1200


def test_immutable_cache_header_only_with_version(staging):
    with_v = ps.staging_image(category="Взуття", name="a.jpg", w=400, v="123")
    without = ps.staging_image(category="Взуття", name="a.jpg", w=400, v=None)
    assert "immutable" in with_v.headers["cache-control"]
    assert "immutable" not in without.headers["cache-control"]


def test_attach_passes_crop_edits_by_file_name(staging, monkeypatch):
    """Кадр 1:1 із редактора доїжджає до add_photos разом із файлом."""
    seen = {}
    monkeypatch.setattr(ps, "add_photos", lambda pnum, cat, sources, kind: seen.update(
        src={s[1]: s[2] for s in sources}) or {"added": len(sources), "errors": []})
    monkeypatch.setattr(ps, "resolve_category", lambda pnum, t: "Взуття")
    monkeypatch.setattr(ps, "invalidate_image_list_cache", lambda *a: None)
    import types
    fake = types.ModuleType("routers.products"); fake._invalidate_photo_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "routers.products", fake)
    monkeypatch.setitem(sys.modules, "backend.routers.products", fake)
    prod = SimpleNamespace(id=7, productnumber="#Ф4400", type=None)
    db = SimpleNamespace(query=lambda m: _Q([prod]))
    crop = {"rotate": 90, "crop": {"x": 0.1, "y": 0, "w": 0.8, "h": 1}}
    ps.staging_attach({"category": "Взуття", "productnumber": "Ф4400", "files": ["a.jpg", "b.jpg"],
                       "edits": {"a.jpg": crop}}, db=db)
    assert seen["src"]["a.jpg"] == {"rotate": 90, "crop": {"x": 0.1, "y": 0.0, "w": 0.8, "h": 1.0}}
    assert seen["src"]["b.jpg"] is None


def test_attach_refuses_garbage_edit_and_keeps_files(staging):
    db = SimpleNamespace(query=lambda m: _Q([SimpleNamespace(id=7, productnumber="#Ф4400", type=None)]))
    with pytest.raises(HTTPException) as e:
        ps.staging_attach({"category": "Взуття", "productnumber": "Ф4400", "files": ["a.jpg"],
                           "edits": {"a.jpg": {"crop": {"x": 5, "y": 0, "w": 1, "h": 1}}}}, db=db)
    assert e.value.status_code == 400
    assert (staging / "Взуття" / "a.jpg").exists()


def test_api_middleware_keeps_explicit_immutable_but_blocks_the_rest():
    """`/api/*` за замовчуванням не кешується (свіжі дані), але мініатюра з
    `?v=` сама оголошує себе immutable — middleware не має це затирати."""
    from fastapi import FastAPI
    from fastapi.responses import Response as R
    from fastapi.testclient import TestClient
    from backend.app import main as m
    app = FastAPI()
    app.middleware("http")(m.no_cache_api)
    app.get("/api/x")(lambda: {"ok": 1})
    app.get("/api/img")(lambda: R(b"x", headers={"Cache-Control": "private, max-age=31536000, immutable"}))
    c = TestClient(app)
    assert "no-store" in c.get("/api/x").headers["cache-control"]
    assert c.get("/api/img").headers["cache-control"] == "private, max-age=31536000, immutable"


def test_added_time_falls_back_to_ctime(staging, monkeypatch):
    """«Дата додавання» Finder — лише на macOS; деінде (і при збої) — ctime."""
    import os
    p = staging / "Взуття" / "a.jpg"
    st = os.stat(p)
    monkeypatch.setattr(ps, "_added_time", None)
    assert ps._file_added(str(p), st) == int(st.st_ctime)
    monkeypatch.setattr(ps, "_added_time", lambda path: (_ for _ in ()).throw(OSError("x")))
    assert ps._file_added(str(p), st) == int(st.st_ctime)
    monkeypatch.setattr(ps, "_added_time", lambda path: 1_700_000_000)
    assert ps._file_added(str(p), st) == 1_700_000_000
