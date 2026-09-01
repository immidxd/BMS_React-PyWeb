import asyncio

from backend.services import auto_publish


def _configs(**flags):
    return [{"platform": name, "auto_publish": value} for name, value in flags.items()]


def _patch(monkeypatch, *, configs, publish, drafts=()):
    """Підмінити конфіги, пошук прострочених чернеток і саме відправлення.

    `drafts` — те, що поверне `_due_drafts`. Раніше цей список приходив ззовні
    як `created` (щойно створені чернетки), і саме через це автопублікація
    померла з появою хмарного воркера: він створював чернетку першим, локальний
    `created` лишався порожнім, і крок відправлення навіть не дивився в базу.
    Тепер джерело — стан чернетки в базі, тож у тестах підміняємо пошук.
    """
    class _Scheduler:
        REVIEW_STATUS = "awaiting_review"

        @staticmethod
        def config_rows(_db):
            return configs

    monkeypatch.setattr(auto_publish, "_mod", lambda _name: _Scheduler)
    monkeypatch.setattr(auto_publish, "publish_collection_draft", publish)
    monkeypatch.setattr(auto_publish, "_due_drafts",
                        lambda _db, _kind, _platforms, _age: list(drafts))
    auto_publish._in_flight.clear()


def _run(**kw):
    return asyncio.run(auto_publish.publish_due_drafts(None, kind="collection", **kw))


def test_a_platform_that_still_wants_review_is_left_alone(monkeypatch):
    """Вимкнена галочка — чернетка просто чекає людину, нічого не летить."""
    calls = []

    async def publish(_db, draft_id, *, body=None):
        calls.append(draft_id)
        return {"ok": True}

    _patch(monkeypatch, configs=_configs(viber=False, facebook=True), publish=publish,
           drafts=[{"id": 1, "platform": "viber"}, {"id": 2, "platform": "facebook"}])
    outcome = _run()

    assert calls == [2], "відправити можна лише той майданчик, де перевірку вимкнено"
    assert [row["draft_id"] for row in outcome["sent"]] == [2]


def test_one_refused_platform_does_not_hold_back_the_other(monkeypatch):
    """Часткова невдача — штатний результат: другий канал не має страждати."""
    async def publish(_db, draft_id, *, body=None):
        if draft_id == 1:
            raise auto_publish.PublishRefused("замало доступних товарів")
        return {"ok": True, "job_id": "job-2"}

    _patch(monkeypatch, configs=_configs(viber=True, facebook=True), publish=publish,
           drafts=[{"id": 1, "platform": "viber"}, {"id": 2, "platform": "facebook"}])
    outcome = _run()

    assert [row["draft_id"] for row in outcome["refused"]] == [1]
    assert [row["draft_id"] for row in outcome["sent"]] == [2]


def test_an_unexpected_failure_is_recorded_and_not_raised(monkeypatch):
    """Збій диспетчера не має валити фоновий цикл — чернетка лишиться на перевірці."""
    async def publish(_db, _draft_id, *, body=None):
        raise RuntimeError("dispatcher unreachable")

    _patch(monkeypatch, configs=_configs(viber=True), publish=publish,
           drafts=[{"id": 1, "platform": "viber"}])
    outcome = _run()

    assert outcome["sent"] == []
    assert outcome["failed"][0]["error"] == "dispatcher unreachable"


def test_nothing_due_means_nothing_sent(monkeypatch):
    _patch(monkeypatch, configs=_configs(viber=True), publish=None, drafts=[])
    assert _run() == {"sent": [], "refused": [], "failed": []}


def test_no_platform_wants_automation_means_no_lookup(monkeypatch):
    """Усі галочки вимкнені — до бази по чернетки навіть не ходимо."""
    looked = []

    def _spy(_db, _kind, _platforms, _age):
        looked.append(True)
        return []

    _patch(monkeypatch, configs=_configs(viber=False, facebook=False), publish=None)
    monkeypatch.setattr(auto_publish, "_due_drafts", _spy)
    assert _run() == {"sent": [], "refused": [], "failed": []}
    assert looked == []


# ── Те, через що баг і стався: джерело чернетки не має значення ──────────────
def test_a_draft_this_process_did_not_create_is_still_published(monkeypatch):
    """Головний регрес: чернетку створив хмарний воркер, а не локальний цикл.

    30.08.2026 підбірка провисіла на перевірці 5,5 години, поки її не
    затвердили руками — бо відправлення дивилось у список `created`, а він був
    порожній: воркер устиг вставити рядок першим, і локальний генератор через
    `ON CONFLICT (platform, scheduled_for) DO NOTHING` не створив нічого.
    """
    sent = []

    async def publish(_db, draft_id, *, body=None):
        sent.append(draft_id)
        return {"ok": True, "job_id": "job-7829"}

    _patch(monkeypatch, configs=_configs(viber=True), publish=publish,
           drafts=[{"id": 7829, "platform": "viber"}])
    outcome = _run()

    assert sent == [7829]
    assert outcome["sent"][0]["job_id"] == "job-7829"


def test_the_same_draft_is_not_sent_twice_by_overlapping_cycles(monkeypatch):
    """Цикл бігає кожні 5 хв, а рендер із диспетчером бувають довшими.

    Поки перше відправлення в польоті, чернетка ще `awaiting_review`, тож
    наступний оберт узяв би її знову.
    """
    started, release = [], asyncio.Event()

    async def publish(_db, draft_id, *, body=None):
        started.append(draft_id)
        await release.wait()
        return {"ok": True}

    _patch(monkeypatch, configs=_configs(viber=True), publish=publish,
           drafts=[{"id": 5, "platform": "viber"}])

    async def scenario():
        first = asyncio.create_task(auto_publish.publish_due_drafts(None, kind="collection"))
        await asyncio.sleep(0)          # дати першому дійти до відправлення
        second = await auto_publish.publish_due_drafts(None, kind="collection")
        release.set()
        await first
        return second

    second = asyncio.run(scenario())
    assert started == [5], "другий оберт не має чіпати чернетку, що вже в польоті"
    assert second == {"sent": [], "refused": [], "failed": []}


def test_a_failed_send_does_not_lock_the_draft_forever(monkeypatch):
    """Збій має звільняти чернетку, інакше повтор — сенс якого в цьому і є —
    не відбудеться до перезапуску BMS."""
    async def publish(_db, _draft_id, *, body=None):
        raise RuntimeError("dispatcher unreachable")

    _patch(monkeypatch, configs=_configs(viber=True), publish=publish,
           drafts=[{"id": 9, "platform": "viber"}])
    _run()
    assert auto_publish._in_flight == set(), "чернетка мусить звільнитись у finally"

    ok = []

    async def publish_ok(_db, draft_id, *, body=None):
        ok.append(draft_id)
        return {"ok": True}

    monkeypatch.setattr(auto_publish, "publish_collection_draft", publish_ok)
    _run()
    assert ok == [9], "наступний оберт мусить повторити спробу"


def test_the_window_starts_when_the_draft_appears_not_when_its_slot_was(monkeypatch):
    """Розклад спрацьовує при старті BMS, тож слот часто вже в минулому:
    01.09.2026 слоти 10:00–11:36 отримали рядки, створені о 14:51. Рахувати
    вікно лише від слота означало б, що чернетка прострочена ще до появи — а
    до появи публікувати не було чого.

    Умова живе в SQL, тож перевіряємо саме її текст: підміна `_due_drafts`
    (як у решті тестів) цю логіку обійшла б і нічого б не довела.
    """
    import inspect
    src = inspect.getsource(auto_publish._due_drafts)
    assert "GREATEST(scheduled_for, created_at)" in src
    assert "scheduled_for <= now()" in src, "прострочені все одно лише ті, чий слот настав"
