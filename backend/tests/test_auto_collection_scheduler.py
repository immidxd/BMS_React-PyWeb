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

