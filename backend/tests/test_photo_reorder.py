"""Перестановка фото: правильний вміст на позиціях і заливка лише змінених у R2.

Раніше кожне перетягування переливало в R2 УСІ фото товару по черзі — звідси
секунди й десятки секунд очікування. Фото, що лишилось на своєму місці, має в
R2 той самий ключ із тим самим вмістом, тож його переливати не треба.
"""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import photo_manager as pm  # noqa: E402

CAT = "Взуття"


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MIRROR_ROOT", tmp_path)
    (tmp_path / CAT).mkdir()
    uploaded = []
    lock = threading.Lock()

    def fake_upload(path, key, **_k):
        with open(path, "rb") as fh:
            body = fh.read()
        with lock:
            uploaded.append((key, body))

    monkeypatch.setattr(pm.r2_storage, "is_enabled", lambda: True)
    monkeypatch.setattr(pm.r2_storage, "upload_file", fake_upload)
    monkeypatch.setattr(pm.r2_storage, "object_exists", lambda key: True)
    monkeypatch.setattr(pm.r2_storage, "delete", lambda key: None)
    monkeypatch.setattr(pm, "_invalidate_r2_index", lambda: None)
    return tmp_path / CAT, uploaded


def c(name):
    return f"content-of-{name}".encode()


def _seed(d, names):
    for n in names:
        (d / n).write_bytes(f"content-of-{n}".encode())


def test_swap_two_uploads_only_those_two(mirror):
    d, uploaded = mirror
    _seed(d, ["Ф1_01.webp", "Ф1_02.webp", "Ф1_03.webp", "Ф1_04.webp"])
    out = pm.reorder_photos("#Ф1", CAT, ["Ф1_01.webp", "Ф1_03.webp", "Ф1_02.webp", "Ф1_04.webp"])
    assert out == ["Ф1_01.webp", "Ф1_02.webp", "Ф1_03.webp", "Ф1_04.webp"]
    # вміст переїхав на нові позиції
    assert (d / "Ф1_02.webp").read_bytes() == c("Ф1_03.webp")
    assert (d / "Ф1_03.webp").read_bytes() == c("Ф1_02.webp")
    assert (d / "Ф1_01.webp").read_bytes() == c("Ф1_01.webp")
    # залито рівно два змінені ключі — і саме з новим вмістом
    assert sorted(uploaded) == sorted([
        (f"{CAT}/Ф1_02.webp", c("Ф1_03.webp")),
        (f"{CAT}/Ф1_03.webp", c("Ф1_02.webp")),
    ])


def test_move_last_to_first_uploads_all_shifted(mirror):
    d, uploaded = mirror
    _seed(d, ["Ф1_001.webp", "Ф1_002.webp", "Ф1_003.webp"])
    pm.reorder_photos("Ф1", CAT, ["Ф1_003.webp", "Ф1_001.webp", "Ф1_002.webp"], kind="real")
    assert [(d / f"Ф1_00{i}.webp").read_bytes() for i in (1, 2, 3)] == [
        c("Ф1_003.webp"), c("Ф1_001.webp"), c("Ф1_002.webp")]
    assert len(uploaded) == 3
    assert {k for k, _ in uploaded} == {f"{CAT}/Ф1_00{i}.webp" for i in (1, 2, 3)}


def test_same_order_uploads_nothing(mirror):
    d, uploaded = mirror
    _seed(d, ["Ф1_01.webp", "Ф1_02.webp"])
    pm.reorder_photos("Ф1", CAT, ["Ф1_01.webp", "Ф1_02.webp"])
    assert uploaded == []
    assert not any(p.name.startswith("__tmp_") for p in d.iterdir())


def test_upload_error_is_raised_after_all_finish(mirror, monkeypatch):
    d, _ = mirror
    _seed(d, ["Ф1_01.webp", "Ф1_02.webp", "Ф1_03.webp"])
    done = []

    def flaky(path, key, **_k):
        if key.endswith("_01.webp"):
            raise RuntimeError("R2 впав")
        done.append(key)

    monkeypatch.setattr(pm.r2_storage, "upload_file", flaky)
    with pytest.raises(RuntimeError):
        pm.reorder_photos("Ф1", CAT, ["Ф1_03.webp", "Ф1_02.webp", "Ф1_01.webp"])
    # решту все одно дочекались (не обірвали посередині)
    assert sorted(done) == [f"{CAT}/Ф1_03.webp"]


def test_mismatched_list_rejected(mirror):
    d, uploaded = mirror
    _seed(d, ["Ф1_01.webp", "Ф1_02.webp"])
    with pytest.raises(ValueError):
        pm.reorder_photos("Ф1", CAT, ["Ф1_01.webp"])
    assert uploaded == []
