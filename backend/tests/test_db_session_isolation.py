"""Регрес: кожен запит має ВЛАСНУ Session.

Історія бага: get_db() віддавав scoped_session(SessionLocal), прив'язану до
потоку. FastAPI виконує синхронну generator-залежність у пулі AnyIO, тому одна
й та сама Session діставалась кільком запитам «у польоті», і db.close() одного
накладався на запит іншого:

    IllegalStateChangeError: Method 'close()' can't be called here;
    method '_connection_for_bind()' is already in progress

Симптом — перші секунди після старту частина запитів (напр. /api/products/filters)
віддавала 500. Тести нижче не потребують живої БД: Session не відкриває
з'єднання, доки не виконано запит.
"""

import os
import threading

# models.database викликає load_dotenv() на імпорті → в os.environ зʼявляються
# DB_HOST/DB_NAME/DB_USER. Інші тести (test_brand_utils, test_parser_brand_linking)
# за наявністю цих змінних перемикаються з skip на інтеграційний режим і б'ють у
# бойову БД. Тому повертаємо оточення таким, яким воно було до імпорту.
_ENV_BEFORE = dict(os.environ)
from backend.models import database as db_mod  # noqa: E402
for _k in set(os.environ) - set(_ENV_BEFORE):
    os.environ.pop(_k, None)


def test_get_db_yields_a_fresh_session_each_call():
    """Два послідовні виклики в ОДНОМУ потоці — різні об'єкти Session.

    Саме це порушував scoped_session: у межах потоку він повертав той самий
    об'єкт, тож два одночасні запити з одного воркера ділили стан.
    """
    # Тримаємо і генератори, і сесії живими до кінця перевірки: після GC
    # звільнена адреса перевикористовується, і id() дав би хибний збіг.
    gens = [db_mod.get_db() for _ in range(3)]
    sessions = [next(g) for g in gens]
    try:
        assert len({id(s) for s in sessions}) == 3, "get_db() перевикористав Session"
    finally:
        for g in gens:
            g.close()  # спрацьовує finally → db.close()


def test_get_db_sessions_are_not_shared_across_threads():
    """Сесії з різних потоків теж мають бути різними об'єктами."""
    grabbed = []          # (generator, session) — тримаємо живими, див. вище
    lock = threading.Lock()

    def grab():
        gen = db_mod.get_db()
        session = next(gen)
        with lock:
            grabbed.append((gen, session))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    try:
        ids = [id(s) for _, s in grabbed]
        assert len(set(ids)) == len(ids), "Session перевикористано між потоками"
    finally:
        for gen, _ in grabbed:
            gen.close()


def test_no_scoped_session_in_database_module():
    """Гард від повернення scoped_session у models/database.py."""
    assert not hasattr(db_mod, "db_session"), (
        "models.database.db_session (scoped_session) повернувся — це той самий "
        "баг: сесія, прив'язана до потоку, розділяється між запитами."
    )
    assert not hasattr(db_mod.Base, "query"), (
        "Base.query = db_session.query_property() тягне за собою scoped_session."
    )
