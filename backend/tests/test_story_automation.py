from datetime import datetime, time, timezone

import pytest

from backend.services import story_automation as sa
from backend.services import story_automation_scheduler as sch


ENABLED_AT = datetime(2026, 8, 21, 19, 0, tzinfo=timezone.utc)   # 22:00 Kyiv
BASE = {
    "enabled": True,
    "enabled_at": ENABLED_AT,
    "local_time": time(11, 0),
    "timezone": "Europe/Kyiv",
    "interval_hours": 24,
}


# ── Ритм ─────────────────────────────────────────────────────────────────────

def test_the_series_starts_at_the_next_wall_clock_time_not_immediately():
    """Увімкнення о 22:00 не має дати Story о 22:00 — розклад стоїть на 11:00."""
    start = sch.series_start(ENABLED_AT, time(11, 0), "Europe/Kyiv")
    assert start == datetime(2026, 8, 22, 8, 0, tzinfo=timezone.utc)   # 11:00 Kyiv


def test_nothing_is_backfilled_before_the_first_slot():
    an_hour_later = datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)
    assert sch.due_slot(BASE, an_hour_later) is None


def test_a_daily_rhythm_lands_on_the_same_wall_clock_time_each_day():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    assert sch.latest_slot(BASE, now) == datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    assert sch.next_slot(BASE, now) == datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)


def test_a_shorter_interval_fires_several_times_a_day():
    config = {**BASE, "interval_hours": 8}
    now = datetime(2026, 8, 22, 17, 30, tzinfo=timezone.utc)          # 20:30 Kyiv
    assert sch.latest_slot(config, now) == datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def test_a_disabled_schedule_is_never_due():
    now = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    assert sch.due_slot({**BASE, "enabled": False}, now) is None


# ── Налаштування ─────────────────────────────────────────────────────────────

def test_publishing_stays_manual_unless_it_is_switched_on_deliberately():
    clean = sch.validate_config({}, BASE)
    assert clean["auto_publish"] is False


def test_an_impossible_rhythm_is_refused():
    with pytest.raises(ValueError):
        sch.validate_config({"interval_hours": 1}, BASE)
    with pytest.raises(ValueError):
        sch.validate_config({"interval_hours": 500}, BASE)
    with pytest.raises(ValueError):
        sch.validate_config({"cooldown_days": 3}, BASE)


# ── Критерії добору ──────────────────────────────────────────────────────────

def test_unknown_criteria_never_reach_the_query():
    """Фільтри приходять із браузера й лягають у SQL — чужого ключа тут бути не може."""
    clean = sa.normalize_filters({
        "brandids": [3, 3, 1], "typeids": ["7"], "max_price": "1500",
        "seasons": [" Літо ", "Літо"],
        "search": "'; DROP TABLE products; --",
        "only_problematic": True,
    })
    assert clean == {
        "brandids": [1, 3], "typeids": [7], "max_price": 1500.0, "seasons": ["Літо"],
    }
    assert "search" not in clean and "only_problematic" not in clean


def test_garbage_values_are_dropped_rather_than_crashing():
    assert sa.normalize_filters({"brandids": "не список", "min_price": "багато"}) == {}
    assert sa.normalize_filters(None) == {}


def test_an_empty_filter_set_reads_as_the_whole_catalogue():
    assert sa.describe_filters(None, {}) == "усі доступні товари"


# ─── Пакет Stories за один слот ──────────────────────────────────────────────

def test_batch_size_is_capped_at_the_measured_meta_limit():
    """10 — не кругле число, а межа з бойового випадку 18.08 (66 завдань → (#4))."""
    from backend.services.story_automation_scheduler import (
        MAX_ITEMS_PER_RUN, validate_config,
    )
    assert MAX_ITEMS_PER_RUN == 10
    assert validate_config({"items_per_run": 10})["items_per_run"] == 10
    for bad in (0, 11, 33):
        try:
            validate_config({"items_per_run": bad})
        except ValueError:
            continue
        raise AssertionError(f"{bad} мало впасти")


def test_batch_defaults_to_a_single_story():
    """Пакет — свідомий вибір людини, а не наслідок оновлення."""
    from backend.services.story_automation_scheduler import validate_config
    assert validate_config({})["items_per_run"] == 1


def test_batch_slots_are_spread_apart_not_fired_together():
    """Слоти пакета рознесені: інакше десять Stories пішли б одним залпом."""
    from datetime import datetime, timedelta, timezone
    from backend.services.story_automation_scheduler import STAGGER_MINUTES

    base = datetime(2026, 8, 26, 7, 0, tzinfo=timezone.utc)
    slots = [base + timedelta(minutes=STAGGER_MINUTES * i) for i in range(10)]

    assert STAGGER_MINUTES >= 1, "нульовий проміжок повертає залп"
    assert len(set(slots)) == 10, "слоти мають бути різні — UNIQUE(platform, scheduled_for)"
    # Десять Stories мають розтягтися помітно довше за виміряні ~3 хвилини,
    # у які Meta вкладає свій ліміт застосунку.
    assert (slots[-1] - slots[0]) > timedelta(minutes=3)



# ─── Студійне фото як умова допуску ──────────────────────────────────────────
#
# Реальні фото («як є», `<pnum>_00N`) знімаються для картки, замірів і Telegram.
# У відкриту стрічку вони не йдуть: знімок на килимі вдома неможливо забрати з
# чужої стрічки назад. Тому вимога стоїть ДВІЧІ — у запиті й перед відправленням.

def _patch_service(monkeypatch, module: str, attr: str, value) -> None:
    """Підмінити атрибут в обох подобах модуля — `services.X` і `backend.services.X`.

    Код під тестом імпортує `services.X` із запасним `backend.services.X`, і це
    ДВА різні об'єкти: підміна лише в одному не діє на інший. Який саме з них
    резолвиться, залежить від того, звідки запущено pytest.
    """
    import importlib

    patched = False
    for prefix in ("services", "backend.services"):
        try:
            mod = importlib.import_module(f"{prefix}.{module}")
        except ImportError:
            continue
        monkeypatch.setattr(mod, attr, value)
        patched = True
    assert patched, f"модуль {module} не імпортується в жодній подобі"


class _FakeResult:
    def __init__(self, rows): self._rows = rows
    def mappings(self): return self
    def all(self): return self._rows


class _RecordingSession:
    """Мінімальна заглушка сесії: запам'ятовує SQL і параметри, нічого не читає."""

    def __init__(self):
        self.sql = ""
        self.params = {}

    def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = params or {}
        return _FakeResult([])


def test_a_home_photo_never_goes_into_the_open_feed(monkeypatch):
    """Товар лише з реальними фото непридатний, хоч знімки в нього і є."""
    for kind, expected in (("official", True), ("real", False), ("none", False)):
        _patch_service(monkeypatch, "telegram_publisher", "_photo_entries",
                       lambda _bms, _k=kind: ([object()], _k))
        assert sa._official_photo_ready({"productnumber": "Ф42"}) is expected, kind


def test_the_studio_requirement_sits_in_the_query_not_after_it(monkeypatch):
    """Умова має бути в SQL, інакше межа пулу зріже добір до товарів із фото.

    Саме так канал і став 07.09.2026: перші 200 кандидатів були поспіль без
    знімків, а перший придатний стояв 202-м — за межею LIMIT.
    """
    _patch_service(monkeypatch, "product_images", "get_official_photo_pnum_set",
                   lambda *_a, **_k: frozenset({"ф3635", "а80"}))
    db = _RecordingSession()
    sa.candidate_rows(db, {}, 30)

    assert ":official_pnums" in db.sql
    assert db.params["official_pnums"] == ["а80", "ф3635"]
    # Донор студійних фото рахується нарівні з власним номером: публікатор бере
    # знімок звідти так само.
    assert "official_photos_from" in db.sql
    # Кирилиця в локалі C не опускається без ICU — без COLLATE збігалися б лише
    # суто цифрові номери, і весь добір по «Ф…» мовчки спорожнів би.
    assert 'COLLATE "und-x-icu"' in db.sql


def test_without_a_known_photo_set_the_slot_is_skipped_rather_than_guessed(monkeypatch):
    """Немає певності про фото — немає публікації. Пропущений слот дешевший."""
    _patch_service(monkeypatch, "product_images", "get_official_photo_pnum_set",
                   lambda *_a, **_k: frozenset())
    db = _RecordingSession()
    sa.candidate_rows(db, {}, 30)

    assert "FALSE" in db.sql
    assert "official_pnums" not in db.params


def test_only_studio_indexes_count_as_studio(tmp_path, monkeypatch):
    """`_01` — студійне, `_001` — реальне, `_def1` — дефект. Різниця в нулях."""
    from backend.services import product_images

    monkeypatch.setenv("PRODUCT_IMAGES_DIR", str(tmp_path))
    for name in ("Ф100_01.jpg", "Ф200_001.jpg", "Ф300_def1.jpg", "Ф400_002.jpg", "Ф400_03.jpg"):
        (tmp_path / name).write_bytes(b"")
    try:
        official = product_images.get_official_photo_pnum_set(force=True)
    finally:
        product_images._OFFICIAL_SET_CACHE["valid"] = False

    assert official == {"ф100", "ф400"}   # Ф400 має і реальні, і студійні — рахується
    assert "ф200" not in official          # лише «як є»
    assert "ф300" not in official          # лише дефект
