import asyncio

from backend.services import auto_publish


def _configs(**flags):
    return [{"platform": name, "auto_publish": value} for name, value in flags.items()]


def _patch(monkeypatch, *, configs, publish):
    class _Scheduler:
        @staticmethod
        def config_rows(_db):
            return configs

    monkeypatch.setattr(auto_publish, "_mod", lambda _name: _Scheduler)
    monkeypatch.setattr(auto_publish, "publish_collection_draft", publish)


def test_a_platform_that_still_wants_review_is_left_alone(monkeypatch):
    """Вимкнена галочка — чернетка просто чекає людину, нічого не летить."""
    calls = []

    async def publish(_db, draft_id, *, body=None):
        calls.append(draft_id)
        return {"ok": True}

    _patch(monkeypatch, configs=_configs(viber=False, facebook=True), publish=publish)
    outcome = asyncio.run(auto_publish.publish_due_drafts(
        None,
        [{"id": 1, "platform": "viber"}, {"id": 2, "platform": "facebook"}],
        kind="collection",
    ))

    assert calls == [2], "відправити можна лише той майданчик, де перевірку вимкнено"
    assert [row["draft_id"] for row in outcome["sent"]] == [2]


def test_one_refused_platform_does_not_hold_back_the_other(monkeypatch):
    """Часткова невдача — штатний результат: другий канал не має страждати."""
    async def publish(_db, draft_id, *, body=None):
        if draft_id == 1:
            raise auto_publish.PublishRefused("замало доступних товарів")
        return {"ok": True, "job_id": "job-2"}

    _patch(monkeypatch, configs=_configs(viber=True, facebook=True), publish=publish)
    outcome = asyncio.run(auto_publish.publish_due_drafts(
        None,
        [{"id": 1, "platform": "viber"}, {"id": 2, "platform": "facebook"}],
        kind="collection",
    ))

    assert [row["draft_id"] for row in outcome["refused"]] == [1]
    assert [row["draft_id"] for row in outcome["sent"]] == [2]


def test_an_unexpected_failure_is_recorded_and_not_raised(monkeypatch):
    """Збій диспетчера не має валити фоновий цикл — чернетка лишиться на перевірці."""
    async def publish(_db, _draft_id, *, body=None):
        raise RuntimeError("dispatcher unreachable")

    _patch(monkeypatch, configs=_configs(viber=True), publish=publish)
    outcome = asyncio.run(auto_publish.publish_due_drafts(
        None, [{"id": 1, "platform": "viber"}], kind="collection",
    ))

    assert outcome["sent"] == []
    assert outcome["failed"][0]["error"] == "dispatcher unreachable"


def test_nothing_created_means_nothing_sent(monkeypatch):
    _patch(monkeypatch, configs=_configs(viber=True), publish=None)
    assert asyncio.run(auto_publish.publish_due_drafts(None, [], kind="collection")) == {
        "sent": [], "refused": [], "failed": [],
    }
