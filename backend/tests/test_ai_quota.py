"""Ліміти ШІ з власного обліку: вікно за тихоокеанським часом, розбір 429."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import ai_quota as aq  # noqa: E402


def _429(quota_id=None, value=None, retry="31s", msg="You exceeded your current quota"):
    details = []
    if quota_id:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                                        "quotaId": quota_id, "quotaValue": str(value)}]})
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return "HTTP 429: " + json.dumps({"error": {"code": 429, "message": msg, "status": "RESOURCE_EXHAUSTED",
                                                 "details": details}})


# ── Вікно квоти ─────────────────────────────────────────────────────────────

def test_window_resets_at_pacific_midnight_not_ours():
    """10:00 за Києвом улітку. Ранкові запити о 09:30 — ще ВЧОРАШНЯ квота."""
    now = datetime(2026, 9, 15, 6, 30, tzinfo=timezone.utc)      # 09:30 Київ
    start, nxt = aq.quota_window(now)
    assert start == datetime(2026, 9, 14, 7, 0, tzinfo=timezone.utc)
    assert nxt == datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 15, 7, 30, tzinfo=timezone.utc)     # 10:30 Київ
    assert aq.quota_window(later)[0] == datetime(2026, 9, 15, 7, 0, tzinfo=timezone.utc)


def test_window_survives_the_dst_switch():
    """У листопаді Каліфорнія переходить на зимовий час: північ стає 08:00 UTC."""
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    start, nxt = aq.quota_window(now)
    assert start == datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)   # ще PDT
    assert nxt == datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)     # уже PST


# ── Розбір відмови ──────────────────────────────────────────────────────────

def test_day_quota_is_read_with_its_value():
    q = aq.parse_quota_error(_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", 20))
    assert q["kind"] == "day" and q["quota_value"] == 20 and q["retry_s"] == 31


def test_minute_quota_is_distinguished():
    assert aq.parse_quota_error(_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", 10, "7s")) \
        == {"kind": "minute", "quota_id": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
            "quota_value": 10, "retry_s": 7, "credits_depleted": False}


def test_truncated_legacy_record_is_unknown_not_crash():
    old = 'HTTP 429: {\n  "error": {\n    "code": 429,\n    "message": "You exceeded your current quota, please check'
    q = aq.parse_quota_error(old)
    assert q["kind"] == "unknown" and q["quota_value"] is None


def test_depleted_paid_credits_are_their_own_kind():
    q = aq.parse_quota_error(_429(msg="Your prepayment credits are depleted. Please go to AI Studio"))
    assert q["kind"] == "credits" and q["credits_depleted"]


@pytest.mark.parametrize("err", [None, "", "HTTP 503: overloaded", "KeyError: candidates"])
def test_non_quota_errors_are_ignored(err):
    assert aq.parse_quota_error(err)["kind"] == "unknown"


# ── Стан ────────────────────────────────────────────────────────────────────

class _DB:
    def __init__(self, rows): self.rows = rows
    def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        rows = self.rows
        class _R:
            def fetchall(self_): return rows
            def scalar(self_): return 0.0
        return _R()


def _row(hours_ago, purpose, ok, err=None, now=None):
    return (now - timedelta(hours=hours_ago), purpose, ok, err)


NOW = datetime(2026, 9, 15, 10, 40, tzinfo=timezone.utc)   # 13:40 Київ, вікно з 07:00 UTC


def test_exhausted_only_when_denial_came_after_the_last_success():
    rows = [_row(3, "autofill", True, now=NOW), _row(2, "autofill", True, now=NOW),
            _row(1, "autofill", False, _429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", 20), now=NOW)]
    s = aq.status(_DB(rows), NOW)
    assert s["free"]["used"] == 2 and s["free"]["exhausted"] is True
    assert s["free"]["limit"] == 20 and s["free"]["resets_at"] == "2026-09-16T07:00:00+00:00"
    # успіх ПІСЛЯ відмови — то була хвилинна або тимчасова, квота жива
    rows.append(_row(0.5, "autofill", True, now=NOW))
    assert aq.status(_DB(rows), NOW)["free"]["exhausted"] is False


def test_paid_calls_do_not_count_against_the_free_window():
    rows = [_row(1, "autofill:paid", True, now=NOW), _row(2, "autofill", True, now=NOW)]
    s = aq.status(_DB(rows), NOW, paid_available=True)
    assert s["free"]["used"] == 1 and s["paid"]["available"] is True


def test_yesterdays_calls_stay_in_yesterdays_window():
    rows = [_row(5, "backfill", True, now=NOW),      # 05:40 UTC — до скидання о 07:00
            _row(1, "autofill", True, now=NOW)]
    s = aq.status(_DB(rows), NOW)
    assert s["free"]["used"] == 1 and s["free"]["observed_max"] == 1


def test_minute_pause_is_reported_while_retry_delay_lasts():
    rows = [(NOW - timedelta(seconds=10), "autofill", False,
             _429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", 10, "30s"))]
    assert aq.status(_DB(rows), NOW)["free"]["retry_after_s"] == 20
    assert aq.status(_DB(rows), NOW + timedelta(minutes=1))["free"]["retry_after_s"] == 0


def test_depleted_paid_key_is_visible():
    rows = [_row(1, "autofill:paid", False, _429(msg="Your prepayment credits are depleted."), now=NOW)]
    s = aq.status(_DB(rows), NOW, paid_available=True)
    assert s["paid"]["credits_depleted"] is True
