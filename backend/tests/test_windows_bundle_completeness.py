"""Кожна бойова залежність мусить потрапити у Windows-збірку.

ЧОМУ ЦЕЙ ТЕСТ ІСНУЄ. Пакет можна поставити у venv, дописати в requirements.txt,
переконатись, що все працює локально, — і при цьому НЕ додати його в
`deploy/bms.spec`. PyInstaller його не знайде (динамічні імпорти, бінарні
розширення), збірка складеться без помилки, а на машині користувача модуль
тихо не імпортується. Саме так свого часу поза бандлом опинились httpx і
fontTools; так само мовчки випав би шар штрихкодів.

Тест звіряє ДВА файли, а не код: усе, що оголошено як пряма залежність, або
перелічене у `_COLLECT`, або внесене сюди зі своєї причини.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements.txt"
SPEC = ROOT / "deploy" / "bms.spec"

# Ім'я на PyPI → ім'я для імпорту, коли вони не збігаються.
IMPORT_NAME = {
    "python-dotenv": "dotenv", "beautifulsoup4": "bs4", "Pillow": "PIL",
    "pillow-heif": "pillow_heif", "zxing-cpp": "zxingcpp",
    "google-auth": "google", "google-auth-oauthlib": "google_auth_oauthlib",
    "google-api-python-client": "googleapiclient", "pydantic-core": "pydantic_core",
    "fonttools": "fontTools", "psycopg2-binary": "psycopg2", "APScheduler": "apscheduler",
}

# Пакети, яких у `_COLLECT` свідомо немає. Кожен — з причиною; список має
# лишатись коротким, бо кожен рядок тут це виняток, а не норма.
NOT_BUNDLED = {
    "pytest": "лише тести", "pytest-asyncio": "лише тести",
    "alembic": "міграції накочуються окремо, не з застосунку",
    "pywebview": "збирається власним хуком PyInstaller, не через collect_all",
    "psycopg2-binary": "чисто бінарний драйвер, підтягується як залежність sqlalchemy",
    "websockets": "транзитивна залежність uvicorn, збирається разом із ним",
    "aiohttp": "транзитивна; прямих імпортів у бекенді немає",
    "python-multipart": "starlette імпортує сам, окремого збору не потребує",
    "Jinja2": "транзитивна залежність starlette",
    "MarkupSafe": "транзитивна залежність Jinja2",
    "oauth2client": "уже перелічений у _COLLECT під власним іменем",
}


def _declared() -> list[str]:
    out = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(re.split(r"[=<>!~\[]", line)[0].strip())
    return out


def _collected() -> set[str]:
    src = SPEC.read_text(encoding="utf-8")
    block = src.split("_COLLECT", 1)[1].split("]", 1)[0]
    return set(re.findall(r'"([^"]+)"', block))


def test_requirements_file_is_parseable():
    assert len(_declared()) > 20, "requirements.txt раптом майже порожній"


def test_spec_collect_list_is_parseable():
    assert len(_collected()) > 15, "не вдалося прочитати _COLLECT із bms.spec"


@pytest.mark.parametrize("package", _declared())
def test_every_dependency_is_bundled_or_excused(package):
    """Або в `_COLLECT`, або у списку винятків із поясненням. Третього нема."""
    if package in NOT_BUNDLED:
        assert NOT_BUNDLED[package], f"{package}: виняток без причини"
        return
    name = IMPORT_NAME.get(package, package)
    assert name in _collected(), (
        f"«{package}» оголошено в requirements.txt, але його немає ані в "
        f"_COLLECT у deploy/bms.spec, ані в NOT_BUNDLED. На Windows цей модуль "
        f"тихо не імпортується. Додайте «{name}» у _COLLECT або внесіть пакет "
        f"у NOT_BUNDLED із поясненням, чому бандл його не потребує."
    )


def test_barcode_layer_reaches_the_windows_build():
    """Іменна перевірка щойно доданого шару — щоб не покладатись на загальну."""
    assert "zxingcpp" in _collected()
    assert "zxing-cpp" in _declared()
