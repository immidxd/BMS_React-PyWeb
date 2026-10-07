"""Індекс R2: застарілий віддається одразу, оновлення — у фоні, зміни — латками.

Раніше повний лістинг бакета (секунди) чекав запит, що першим приходив після
TTL або після будь-якої зміни фото, — картка відкривалась із порожньою
галереєю на 4+ с, а через 3.5 с показувала «Фото відсутнє».
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import product_images as pi  # noqa: E402


class _R2:
    def __init__(self, keys):
        self.keys = list(keys)
        self.calls = 0
        self.gate = None          # threading.Event — затримати лістинг
        self.started = threading.Event()

    def is_enabled(self):
        return True

    def list_keys_with_etag(self, prefix=""):
        self.calls += 1
        snapshot = list(self.keys)
        self.started.set()
        if self.gate is not None:
            self.gate.wait(5)
        return [(k, "e") for k in snapshot]


@pytest.fixture
def r2(monkeypatch):
    fake = _R2(["Взуття/Ф1_01.webp", "Взуття/Ф1_02.webp"])
    monkeypatch.setattr(pi, "_r2", lambda: fake)
    monkeypatch.setattr(pi, "_publish_index_to_db", lambda rows: None)
    monkeypatch.setattr(pi, "_R2_INDEX", {"at": 0.0, "by_pnum": {}})
    monkeypatch.setattr(pi, "_R2_STATE", {"loaded": False, "gen": 0, "patches": [],
                                          "refreshing": False, "must_sync": False})
    return fake


def _wait_idle(timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        with pi._R2_INDEX_LOCK:
            if not pi._R2_STATE["refreshing"]:
                return
        time.sleep(0.01)
    raise AssertionError("фонове оновлення не завершилось")


def test_first_load_is_synchronous(r2):
    assert sorted(pi._r2_index()["ф1"]) == ["Взуття/Ф1_01.webp", "Взуття/Ф1_02.webp"]
    assert r2.calls == 1


def test_stale_index_is_served_immediately_and_refreshed_in_background(r2, monkeypatch):
    pi._r2_index()
    r2.keys.append("Взуття/Ф2_01.webp")
    with pi._R2_INDEX_LOCK:
        pi._R2_INDEX["at"] = 0.0          # TTL минув
    r2.gate = threading.Event()
    t0 = time.monotonic()
    idx = pi._r2_index()                  # не чекає лістингу
    assert time.monotonic() - t0 < 0.5
    assert "ф2" not in idx
    r2.gate.set()
    _wait_idle()
    assert "ф2" in pi._r2_index()


def test_patch_shows_added_and_hides_removed_at_once(r2):
    pi._r2_index()
    pi.r2_index_patch(added=["Взуття/Ф1_03.webp"], removed=["Взуття/Ф1_01.webp"])
    assert sorted(pi._r2_index()["ф1"]) == ["Взуття/Ф1_02.webp", "Взуття/Ф1_03.webp"]
    _wait_idle()


def test_delete_during_listing_does_not_resurrect(r2):
    """Лістинг почався ДО видалення і ще бачить файл — латка має перемогти."""
    pi._r2_index()
    with pi._R2_INDEX_LOCK:
        pi._R2_INDEX["at"] = 0.0
    r2.gate = threading.Event()
    pi._r2_index()                        # стартує фоновий лістинг (старий знімок)
    assert r2.started.wait(2)
    r2.keys.remove("Взуття/Ф1_01.webp")   # видалили в R2
    pi.r2_index_patch(removed=["Взуття/Ф1_01.webp"])
    r2.gate.set()
    _wait_idle()
    assert pi._r2_index()["ф1"] == ["Взуття/Ф1_02.webp"]


def test_non_product_keys_ignored_by_patch(r2):
    pi._r2_index()
    pi.r2_index_patch(added=["derived/Ф1_prom.webp", "Взуття/readme.txt"])
    assert sorted(pi._r2_index()["ф1"]) == ["Взуття/Ф1_01.webp", "Взуття/Ф1_02.webp"]
    _wait_idle()


def test_unknown_change_forces_sync_rebuild(r2):
    pi._r2_index()
    r2.keys.append("Взуття/Ф3_01.webp")
    pi.invalidate_r2_index()
    assert "ф3" in pi._r2_index()         # чекає, бо зміна невідома


def test_prewarm_builds_in_background(r2):
    pi.prewarm_r2_index()
    _wait_idle()
    assert pi._R2_STATE["loaded"] and "ф1" in pi._R2_INDEX["by_pnum"]
