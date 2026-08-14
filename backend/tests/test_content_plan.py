"""Розбір задач TaskNotes → слоти публікацій.

Фікстури повторюють реальні нотатки з теки `Brandstore/Контент-план/Публікації`,
тому тест ловить розходження саме з тим форматом, який веде користувач.
"""

from datetime import datetime

from backend.services import content_plan


def _task(**overrides):
    task = {
        "path": "Brandstore/Контент-план/Публікації/2026-08-17 — Telegram топ-5 товарів.md",
        "title": "Telegram — викласти топ-5 товарів",
        "status": "planned",
        "scheduled": "2026-08-17T18:30:00",
        "contexts": ["telegram"],
        "tags": ["task", "brandstore-content"],
    }
    task.update(overrides)
    return task


def test_telegram_top5_slot_reads_channel_and_count_from_real_note():
    slot = content_plan.slot_from_task(_task())

    assert slot["channel"] == "telegram"
    assert slot["post_format"] == "post"
    assert slot["rubric"] == "top"
    assert slot["product_count"] == 5
    assert slot["scheduled_at"] == datetime(2026, 8, 17, 18, 30)
    assert slot["plan_status"] == "planned"
    assert slot["plan_completed"] is False


def test_stories_context_wins_over_default_instagram_format():
    slot = content_plan.slot_from_task(_task(
        title="Instagram + Facebook — викласти Stories",
        contexts=["instagram", "stories"],
    ))

    assert slot["channel"] == "instagram"
    assert slot["post_format"] == "stories"
    assert slot["product_count"] == 1


def test_instagram_without_format_context_defaults_to_feed():
    slot = content_plan.slot_from_task(_task(
        title="Instagram + Facebook — основний пост",
        contexts=["instagram"],
    ))

    assert slot["post_format"] == "feed"
    assert slot["rubric"] == "general"


def test_viber_digest_slot_gets_collage_format():
    slot = content_plan.slot_from_task(_task(
        title="Viber — викласти добірку товарів",
        contexts=["viber"],
    ))

    assert slot["channel"] == "viber"
    assert slot["post_format"] == "collage"
    assert slot["rubric"] == "digest"
    assert slot["product_count"] == 6


def test_planning_task_is_not_a_publication_slot():
    """«Спланувати тиждень» не має каналу — у контент-плані BMS їй не місце."""
    slot = content_plan.slot_from_task(_task(
        title="Спланувати публікації на тиждень",
        contexts=["planning"],
        recurrence="FREQ=WEEKLY;BYDAY=SU",
    ))

    assert slot is None


def test_completed_plan_status_matches_user_done_status():
    slot = content_plan.slot_from_task(_task(status="done"))

    assert slot["plan_completed"] is True


def test_channel_falls_back_to_title_when_contexts_missing():
    slot = content_plan.slot_from_task(_task(contexts=[]))

    assert slot["channel"] == "telegram"


def test_task_without_path_is_skipped():
    task = _task()
    del task["path"]

    assert content_plan.slot_from_task(task) is None


def test_scheduled_date_without_time_is_accepted():
    slot = content_plan.slot_from_task(_task(scheduled="2026-08-17"))

    assert slot["scheduled_at"] == datetime(2026, 8, 17, 0, 0)


def test_explicit_count_in_title_overrides_channel_default():
    slot = content_plan.slot_from_task(_task(title="Telegram — топ-3 товарів"))

    assert slot["product_count"] == 3


def test_absurd_count_falls_back_to_channel_default():
    """«топ-100» — явна помилка, а не намір завалити канал сотнею постів."""
    slot = content_plan.slot_from_task(_task(title="Telegram — топ-99 товарів"))

    assert slot["product_count"] == 5


def test_due_is_used_when_scheduled_is_absent():
    task = _task(scheduled=None, due="2026-08-19T19:00:00")

    assert content_plan.slot_from_task(task)["scheduled_at"] == datetime(2026, 8, 19, 19, 0)


# ── Вебхуки ──────────────────────────────────────────────────────────────────

def _signature(raw_body: bytes, secret: str) -> str:
    import hmac, hashlib
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


def test_webhook_signature_matches_tasknotes_hmac():
    body = b'{"event":"task.updated"}'

    assert content_plan.verify_webhook_signature(body, _signature(body, "s3cret"), "s3cret")


def test_webhook_signature_rejects_tampered_body():
    secret = "s3cret"
    signature = _signature(b'{"event":"task.updated"}', secret)

    assert not content_plan.verify_webhook_signature(b'{"event":"task.deleted"}', signature, secret)


def test_webhook_signature_rejects_empty_secret_or_signature():
    body = b"{}"

    assert not content_plan.verify_webhook_signature(body, _signature(body, ""), "")
    assert not content_plan.verify_webhook_signature(body, "", "s3cret")


def test_task_is_found_inside_nested_webhook_envelope():
    payload = {"event": "task.updated", "vault": "Obsidian Vault",
               "data": {"task": _task()}}

    found = content_plan.extract_task_from_payload(payload)

    assert found and found["path"].endswith("Telegram топ-5 товарів.md")


def test_envelope_without_task_returns_none():
    assert content_plan.extract_task_from_payload({"event": "pomodoro.started"}) is None
