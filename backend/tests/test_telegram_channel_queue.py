"""Черга каналу BrandStore: план у BMS → розклад Telegram → перевірка доставки.

ЧОМУ ЦЕ ІСНУЄ. 16.10.2026 перевірка на живих даних показала: 20 із 95
запланованих через `schedule=` форвардів Telegram мовчки не відправив (оригінал у
форумі живий), а розклад Telegram тримає лише 100 повідомлень (кожне фото —
окремо). Тому план живе в BMS, у Telegram іде лише найближче, а сторож
перевіряє сам канал і досилає. Тести фіксують саме ці гарантії.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services import telegram_channel_queue as q  # noqa: E402
from services import telegram_publisher as tp  # noqa: E402

UTC = timezone.utc


def _at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=tp.KYIV_TZ)


def _msg(mid, date, text=""):
    return SimpleNamespace(id=mid, date=date, message=text)


# ── Пошук доставленого поста в каналі ────────────────────────────────────────

def test_delivered_is_found_by_exact_source_caption():
    item = {"scheduled_at": _at(17, 9), "product_number": "#Ф4180"}
    source = _msg(9328, _at(11, 11), "👟 Caprice • ботинки\n\nПиши `#Ф4180`")
    window = [
        _msg(50, _at(17, 9, 0), "👟 Caprice • ботинки\n\nПиши `#Ф4180`"),
        _msg(49, _at(17, 8, 0), "інший пост"),
    ]
    assert q.find_delivered(item, window, source).id == 50


def test_delivered_falls_back_to_number_token_when_source_deleted():
    item = {"scheduled_at": _at(17, 9), "product_number": "#Ф4180"}
    window = [_msg(50, _at(17, 9, 1), "… Пиши #Ф4180 менеджеру")]
    assert q.find_delivered(item, window, None).id == 50


def test_number_token_never_matches_a_longer_or_unprefixed_number():
    """'#4180' і '#Ф4180' у цій базі — РІЗНІ товари; '#Ф41801' — теж інший."""
    item = {"scheduled_at": _at(17, 9), "product_number": "#Ф4180"}
    window = [
        _msg(51, _at(17, 9, 1), "Пиши #Ф41801 менеджеру"),
        _msg(52, _at(17, 9, 2), "Пиши #4180 менеджеру"),
    ]
    assert q.find_delivered(item, window, None) is None


def test_old_post_of_the_same_product_does_not_count_as_delivery():
    """Давній пост того самого товару не доводить, що вийшов САМЕ запланований."""
    item = {"scheduled_at": _at(17, 9), "product_number": "#Ф4180"}
    window = [_msg(10, _at(1, 9), "Пиши #Ф4180 менеджеру")]
    assert q.find_delivered(item, window, None) is None


# ── Перенесення ─────────────────────────────────────────────────────────────

def test_next_day_same_time_keeps_kyiv_clock_time():
    now = _at(17, 23, 30).astimezone(UTC)
    moved = q.next_day_same_time(_at(17, 18, 0), now)
    assert moved.astimezone(tp.KYIV_TZ).strftime("%d.%m %H:%M") == "18.10 18:00"

    morning = _at(17, 7, 0).astimezone(UTC)
    same_day = q.next_day_same_time(_at(16, 18, 0), morning)
    assert same_day.astimezone(tp.KYIV_TZ).strftime("%d.%m %H:%M") == "17.10 18:00"


def test_free_slot_moves_past_busy_minutes_from_db_and_batch():
    class Db:
        def execute(self, *_a, **_k):
            return SimpleNamespace(fetchall=lambda: [(_at(17, 9, 0),), (_at(17, 9, 2),)])

    when = q.free_slot(Db(), _at(17, 9, 0), taken=[_at(17, 9, 4)])
    assert when == _at(17, 9, 6)
    assert q.free_slot(Db(), _at(17, 12, 0)) == _at(17, 12, 0)


# ── Сторож: перевірка й досилання ───────────────────────────────────────────

class FakeClient:
    def __init__(self, *, scheduled=(), channel=(), sources=None, forward_error=None):
        self.scheduled = list(scheduled)      # ids у розкладі Telegram
        self.channel = list(channel)          # повідомлення каналу (новіші першими)
        self.sources = sources or {}
        self.forward_error = forward_error
        self.forwarded = []
        self.unscheduled = []
        self.next_id = 900

    async def __call__(self, request):
        name = type(request).__name__
        if name == "GetScheduledHistoryRequest":
            return SimpleNamespace(messages=[SimpleNamespace(id=i) for i in self.scheduled],
                                   count=len(self.scheduled))
        if name == "DeleteScheduledMessagesRequest":
            self.unscheduled.extend(request.id)
            self.scheduled = [i for i in self.scheduled if i not in request.id]
            return None
        raise AssertionError(name)

    async def get_entity(self, peer):
        return peer

    async def get_messages(self, _peer, ids=None, **_kw):
        if isinstance(ids, list):
            if all(i in self.sources for i in ids):
                return [self.sources.get(i) for i in ids]
            return [self.sources.get(i) or next((m for m in self.channel if m.id == i), None)
                    for i in ids]
        return self.sources.get(ids)

    async def iter_messages(self, _peer, limit=None):
        for message in self.channel:
            yield message

    async def forward_messages(self, *, entity, messages, from_peer, schedule=None, silent=False):
        if self.forward_error:
            raise self.forward_error
        self.forwarded.append({"messages": list(messages), "schedule": schedule, "silent": silent})
        out = []
        for _ in messages:
            self.next_id += 1
            out.append(SimpleNamespace(id=self.next_id, date=schedule, message="текст"))
        if schedule is None:
            self.channel.insert(0, out[0])
        return out


def _harness(monkeypatch, rows, client, available=True):
    """Підміняє БД: _active_rows віддає рядки, _update збирає зміни."""
    updates = []
    state = {r["id"]: dict(r) for r in rows}

    def fake_active(_db, where, params=None):
        params = params or {}
        out = []
        for row in state.values():
            ok = False
            if "s.state = 'queued' AND s.scheduled_at <= :before" in where:
                ok = row["state"] == "queued" and row["scheduled_at"] <= params["before"]
            elif "s.state = 'planned' AND s.scheduled_at <= :lead" in where:
                ok = row["state"] == "planned" and row["scheduled_at"] <= params["lead"]
            elif "s.state = 'planned' AND s.scheduled_at > :lead" in where:
                ok = row["state"] == "planned" and row["scheduled_at"] > params["lead"]
            if ok:
                out.append({**row, "available": row.get("available", available)})
        return sorted(out, key=lambda r: r["scheduled_at"])

    def fake_update(_db, item_id, **fields):
        updates.append((item_id, fields))
        state[item_id].update(fields)

    monkeypatch.setattr(q, "_active_rows", fake_active)
    monkeypatch.setattr(q, "_update", fake_update)
    monkeypatch.setattr(q, "_record_channel_post", lambda *_a, **_k: None)
    monkeypatch.setattr(q, "free_slot", lambda _db, when, **_k: when)
    monkeypatch.setattr(tp, "BATCH_POST_GAP_SEC", 0)
    scanner = SimpleNamespace(client=client)
    return scanner, updates, state


def _row(**kw):
    base = {
        "id": 1, "product_id": 10, "product_number": "#Ф4180", "state": "queued",
        "scheduled_at": _at(17, 9), "original_scheduled_at": _at(17, 9),
        "source_chat_id": tp.FORUM_CHAT_ID, "source_message_id": 9328,
        "source_message_ids": [9328, 9329], "photo_count": 2, "tg_scheduled_ids": [501, 502],
        "attempts": 0, "silent": False,
    }
    base.update(kw)
    return base


def test_delivered_post_is_marked_sent_with_channel_message_id(monkeypatch):
    source = _msg(9328, _at(11, 11), "Пиши `#Ф4180`")
    client = FakeClient(channel=[_msg(4900, _at(17, 9, 0), "Пиши `#Ф4180`")],
                        sources={9328: source})
    scanner, updates, state = _harness(monkeypatch, [_row()], client)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 10)))

    assert stats["sent"] == 1
    assert state[1]["state"] == "sent" and state[1]["channel_message_id"] == 4900
    assert state[1]["sent_via"] == "telegram"
    assert client.forwarded == []


def test_silently_dropped_post_is_resent_by_bms_the_same_day(monkeypatch):
    """Саме той збій, через який власник бачив «помилкові» у Запланованих."""
    source = _msg(9328, _at(11, 11), "Пиши `#Ф4180`")
    client = FakeClient(channel=[_msg(4899, _at(16, 18), "вчорашній пост")],
                        sources={9328: source})
    scanner, _updates, state = _harness(monkeypatch, [_row()], client)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 10)))

    assert stats["resent"] == 1
    assert client.forwarded == [{"messages": [9328, 9329], "schedule": None, "silent": False}]
    assert state[1]["state"] == "sent" and state[1]["sent_via"] == "bms"
    assert state[1]["attempts"] == 1
    assert "BMS доставив сам" in state[1]["last_error"]


def test_post_still_waiting_in_telegram_schedule_is_left_alone(monkeypatch):
    client = FakeClient(scheduled=[501, 502], sources={9328: _msg(9328, _at(11, 11), "x")})
    scanner, updates, _state = _harness(monkeypatch, [_row()], client)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 5)))

    assert stats["waiting"] == 1 and updates == [] and client.forwarded == []


def test_post_stuck_in_schedule_is_unscheduled_and_sent_by_bms(monkeypatch):
    client = FakeClient(scheduled=[501, 502], sources={9328: _msg(9328, _at(11, 11), "x")})
    scanner, _updates, state = _harness(monkeypatch, [_row()], client)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 40)))

    assert client.unscheduled == [501, 502], "інакше Telegram міг би вислати дубль"
    assert stats["resent"] == 1 and state[1]["state"] == "sent"


def test_failure_found_at_night_is_moved_to_next_day_not_sent(monkeypatch):
    client = FakeClient(sources={9328: _msg(9328, _at(11, 11), "x")})
    row = _row(scheduled_at=_at(17, 18), original_scheduled_at=_at(17, 18))
    scanner, _updates, state = _harness(monkeypatch, [row], client)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 23, 0)))

    assert client.forwarded == []
    assert stats["rescheduled"] == 1 and state[1]["state"] == "planned"
    assert state[1]["scheduled_at"].astimezone(tp.KYIV_TZ).strftime("%d.%m %H:%M") == "18.10 18:00"
    assert "перенесено на 18.10 18:00" in state[1]["last_error"]


def test_sold_product_is_cancelled_instead_of_resent(monkeypatch):
    client = FakeClient(sources={9328: _msg(9328, _at(11, 11), "x")})
    scanner, _updates, state = _harness(monkeypatch, [_row()], client, available=False)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 10)))

    assert stats["cancelled"] == 1 and state[1]["state"] == "cancelled"
    assert client.forwarded == []


def test_deleted_source_fails_loudly_instead_of_retrying_forever(monkeypatch):
    client = FakeClient(sources={})
    scanner, _updates, state = _harness(monkeypatch, [_row()], client)

    stats = asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 10)))

    assert stats["failed"] == 1 and state[1]["state"] == "failed"
    assert "Оригінал у форумі видалено" in state[1]["last_error"]


def test_attempts_are_capped(monkeypatch):
    client = FakeClient(sources={9328: _msg(9328, _at(11, 11), "x")})
    scanner, _updates, state = _harness(monkeypatch, [_row(attempts=q.MAX_ATTEMPTS - 1)], client)

    asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 9, 10)))

    assert state[1]["state"] == "failed" and client.forwarded == []


def test_backlog_after_sleeping_mac_is_not_dumped_into_channel_at_once(monkeypatch):
    sources = {9000 + i: _msg(9000 + i, _at(11, 11), "x") for i in range(6)}
    rows = [_row(id=i + 1, state="planned", tg_scheduled_ids=None, source_message_id=9000 + i,
                 source_message_ids=[9000 + i], scheduled_at=_at(17, 9, i * 2),
                 product_number=f"#Ф{i}") for i in range(6)]
    client = FakeClient(sources=sources)
    scanner, _updates, state = _harness(monkeypatch, rows, client)

    asyncio.run(q.verify_and_recover(object(), scanner, now=_at(17, 10, 0)))

    assert len(client.forwarded) == q.FALLBACK_PER_TICK
    assert sum(1 for r in state.values() if r["state"] == "planned") == 6 - q.FALLBACK_PER_TICK


# ── Розклад Telegram: лише найближче, у межах бюджету місць ──────────────────

def test_top_up_queues_only_what_fits_the_telegram_budget(monkeypatch):
    monkeypatch.setattr(q, "QUEUE_BUDGET", 12)
    rows = [_row(id=i + 1, state="planned", tg_scheduled_ids=None, photo_count=5,
                 source_message_ids=[100 * i + k for k in range(5)],
                 scheduled_at=datetime.now(UTC) + timedelta(days=1, hours=i)) for i in range(4)]
    client = FakeClient(scheduled=[1])  # одне чуже повідомлення вже в розкладі
    scanner, _updates, state = _harness(monkeypatch, rows, client)

    result = asyncio.run(q.top_up(object(), scanner))

    # 1 + 5 + 5 = 11 ≤ 12; третій альбом (16) уже не влазить
    assert result["queued"] == 2 and result["left_planned"] == 2
    assert [r["state"] for r in sorted(state.values(), key=lambda r: r["id"])] == \
        ["queued", "queued", "planned", "planned"]
    assert all(call["schedule"] is not None for call in client.forwarded)
    assert state[1]["tg_scheduled_ids"] == [901, 902, 903, 904, 905]


def test_top_up_stops_quietly_when_telegram_says_schedule_is_full(monkeypatch):
    class ScheduleTooMuchError(Exception):
        pass

    rows = [_row(state="planned", tg_scheduled_ids=None,
                 scheduled_at=datetime.now(UTC) + timedelta(days=1))]
    client = FakeClient(forward_error=ScheduleTooMuchError("SCHEDULE_TOO_MUCH"))
    scanner, updates, state = _harness(monkeypatch, rows, client)

    result = asyncio.run(q.top_up(object(), scanner))

    assert result["queued"] == 0 and "чекає в плані" in result["note"]
    assert state[1]["state"] == "planned" and updates == []


# ── Інваріанти, які легко зламати ───────────────────────────────────────────

def test_sold_check_also_catches_deleted_products():
    """product_id = NULL (товар видалено) теж має зніматися, інакше сторож
    бачив би роботу вічно й підключався до Telegram кожні 5 хв."""
    src = inspect.getsource(q.mark_sold_items)
    assert "FROM products p, " not in src and "FROM products p\n        WHERE p.id = s.product_id AND" not in src
    assert "NOT EXISTS" in src


def test_idle_watchdog_does_not_touch_telegram(monkeypatch):
    class Db:
        def execute(self, *_a, **_k):
            return SimpleNamespace(fetchone=lambda: (0, 0, 0, 0, 0, 0))

    assert q.needs_telegram(Db()) is False
