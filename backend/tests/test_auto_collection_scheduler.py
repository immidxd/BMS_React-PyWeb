from datetime import datetime, time, timezone

import pytest

from backend.services.auto_collection_scheduler import (
    due_slot,
    latest_weekly_slot,
    next_weekly_slot,
    validate_config,
)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)  # Wednesday, 15:00 Kyiv


def test_weekly_slots_follow_kyiv_wall_clock():
    latest = latest_weekly_slot(
        NOW, weekday=6, local_time=time(10, 0), timezone_name="Europe/Kyiv",
    )
    upcoming = next_weekly_slot(
        NOW, weekday=6, local_time=time(10, 0), timezone_name="Europe/Kyiv",
    )
    assert latest == datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc)
    assert upcoming == datetime(2026, 8, 23, 7, 0, tzinfo=timezone.utc)


def test_newly_enabled_schedule_does_not_backfill_previous_week():
    config = {
        "enabled": True,
        "enabled_at": datetime(2026, 8, 19, 11, 59, tzinfo=timezone.utc),
        "weekday": 6,
        "local_time": time(10, 0),
        "timezone": "Europe/Kyiv",
    }
    assert due_slot(config, NOW) is None


def test_due_slot_recovers_latest_missed_slot_after_restart():
    config = {
        "enabled": True,
        "enabled_at": datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc),
        "weekday": 6,
        "local_time": time(10, 0),
        "timezone": "Europe/Kyiv",
    }
    assert due_slot(config, NOW) == datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc)


def test_config_keeps_manual_review_and_four_supported_periods():
    result = validate_config({
        "enabled": True,
        "weekday": 0,
        "local_time": "09:30",
        "timezone": "Europe/Kyiv",
        "period_days": 30,
        "cooldown_days": 14,
        "item_count": 9,
    })
    assert result["local_time"] == time(9, 30)
    with pytest.raises(ValueError):
        validate_config({**result, "period_days": 15})
    with pytest.raises(ValueError):
        validate_config({**result, "cooldown_days": 7})



# ─── Перевірка складу перед відправленням ────────────────────────────────────

def _draft(selected, reserves, count=3):
    return {
        "platform": "viber",
        "scheduled_for": NOW,
        "selected": [{"productnumber": n, "product_id": i} for i, n in enumerate(selected, 1)],
        "reserves": [{"productnumber": n, "product_id": 100 + i} for i, n in enumerate(reserves, 1)],
        "policy": {"count": count, "period_days": 30, "cooldown_days": 14},
    }


def _patch_pool(monkeypatch, available, blocked=()):
    from backend.services import auto_collection_scheduler as sch
    monkeypatch.setattr(
        sch.auto_collection, "_candidate_rows",
        lambda _db, _period, pool=0: [{"productnumber": n} for n in available],
    )
    monkeypatch.setattr(
        sch.auto_collection, "_cooldown_numbers",
        lambda _db, _cooldown, _slot=None: list(blocked),
    )
    return sch


def test_a_product_sold_since_the_draft_is_replaced_from_the_reserve(monkeypatch):
    """Обіцянка policy.revalidate_before_publish: продане не їде в підбірку."""
    sch = _patch_pool(monkeypatch, available=["#Ф1", "#Ф3", "#Ф9"])
    result = sch.revalidate_draft(None, _draft(["#Ф1", "#Ф2", "#Ф3"], ["#Ф9"]))

    assert result["ok"] is True
    assert [row["productnumber"] for row in result["selected"]] == ["#Ф1", "#Ф3", "#Ф9"]
    assert [row["productnumber"] for row in result["dropped"]] == ["#Ф2"]
    assert [row["productnumber"] for row in result["promoted"]] == ["#Ф9"]
    assert any("#Ф2" in warning for warning in result["warnings"])


def test_a_product_posted_elsewhere_meanwhile_is_dropped_too(monkeypatch):
    """Захист від повторів звіряється заново: чужа підбірка могла зайняти товар."""
    sch = _patch_pool(monkeypatch, available=["#Ф1", "#Ф2", "#Ф3"], blocked=["Ф2"])
    result = sch.revalidate_draft(None, _draft(["#Ф1", "#Ф2", "#Ф3"], []))

    assert [row["productnumber"] for row in result["selected"]] == ["#Ф1", "#Ф3"]
    assert [row["productnumber"] for row in result["dropped"]] == ["#Ф2"]


def test_an_exhausted_reserve_is_reported_but_still_publishable(monkeypatch):
    sch = _patch_pool(monkeypatch, available=["#Ф1", "#Ф3"])
    result = sch.revalidate_draft(None, _draft(["#Ф1", "#Ф2", "#Ф3"], []))

    assert result["ok"] is True
    assert any("резерв вичерпано" in warning for warning in result["warnings"])


def test_a_draft_left_with_one_product_may_not_be_sent(monkeypatch):
    """Сітка з одного товару — не підбірка; краще не відправити нічого."""
    sch = _patch_pool(monkeypatch, available=["#Ф1"])
    result = sch.revalidate_draft(None, _draft(["#Ф1", "#Ф2", "#Ф3"], []))

    assert result["ok"] is False
