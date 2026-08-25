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
