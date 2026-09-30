"""Довгий виклик до Google не має класти запит.

30.09.2026, #Ф4419: користувач побачив «Internal server error» без жодної
підказки. Трасування показало ланцюг:

    call_gemini → requests.post(timeout=180) → TimeoutError
      → летить крізь extract_and_propose у глобальний обробник → 500

а разом із ним — `psycopg2.OperationalError: server closed the connection
unexpectedly`: сесія тримала ЧИТАЛЬНУ транзакцію, поки ми 3 хвилини чекали
мережу, а в Postgres стоїть `idle_in_transaction_session_timeout=120000`.
Тобто база рвала з'єднання ще до того, як модель відповідала.

Відтворювалось двома одночасними натисканнями «Розпізнати».
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest
import requests

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from backend.services import photo_autofill as pa  # noqa: E402


def test_http_timeout_is_below_db_idle_transaction_limit():
    """Другий рубіж: навіть якщо транзакцію хтось забуде звільнити, мережевий
    виклик мусить скінчитись РАНІШЕ, ніж Postgres розірве з'єднання."""
    from backend.models import database

    opts = database.engine.url  # лише щоб імпорт не був «мертвим»
    assert pa.REQUEST_TIMEOUT_S < 120, (
        "idle_in_transaction_session_timeout у database.py = 120 с; "
        "HTTP-таймаут мусить бути меншим"
    )
    assert opts is not None


@pytest.mark.parametrize("err,expected", [
    ("ReadTimeout: HTTPSConnectionPool…", True),
    ("ConnectTimeout: …", True),
    ("ConnectionError: …", True),
    ("SSLError: …", True),
    ("HTTP 400: bad request", False),
    ("JSONDecodeError: x", False),
    (None, False),
])
def test_network_failure_is_recognised(err, expected):
    assert pa._is_network_failure(err) is expected


def test_network_failure_is_worth_another_model():
    """Таймаут — та сама категорія, що й 503: ця модель не відповідає."""
    assert pa._worth_another_model("ReadTimeout: …") is True


def test_timeout_becomes_an_error_field_not_an_exception(monkeypatch, tmp_path):
    """Головне: виняток НЕ летить назовні — інакше 500 без сліду в обліку."""
    def boom(*a, **k):
        raise requests.exceptions.ReadTimeout("The read operation timed out")
    monkeypatch.setattr(pa.requests if hasattr(pa, "requests") else requests, "post", boom, raising=False)
    monkeypatch.setitem(sys.modules, "requests", requests)
    monkeypatch.setattr(requests, "post", boom)

    photo = tmp_path / "Ф1_001.webp"; photo.write_bytes(b"x")
    out = pa.call_gemini("m", "k", [photo], {"type": "object", "properties": {}})

    assert "_error" in out and "ReadTimeout" in out["_error"]
    assert out.get("_network") is True


class _Session:
    """Сесія, яка запам'ятовує, чи звільнили транзакцію."""
    def __init__(self): self.rolled_back = 0
    def rollback(self): self.rolled_back += 1


def test_transaction_is_released_before_the_network_call():
    db = _Session()
    pa._release_db(db)
    assert db.rolled_back == 1, "перед мережевим викликом транзакція має бути закрита"


def test_release_db_never_raises():
    """Збій звільнення не має ламати розпізнавання — воно й так уже в дорозі."""
    class _Bad:
        def rollback(self): raise RuntimeError("no connection")
    pa._release_db(_Bad())   # не кидає
