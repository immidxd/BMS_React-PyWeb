"""Каталог шрифтів пристрою.

Найцінніше тут — не «чи знайшлися шрифти» (це залежить від машини), а
поведінка на межах: токен має чесно розкодовуватись назад, а імпорт — не
приймати шлях кудись поза теками шрифтів. Каталог читає файли з диска, тож
решта перевірок навмисно не залежить від конкретного набору шрифтів у системі.
"""

from __future__ import annotations

import os

import pytest

from backend.services import studio_fonts as sf

# ⚠️ Помилку ловимо через `sf.studio`, а не через власний імпорт `studio`.
# У повному прогоні інші тести встигають додати `backend/` у sys.path, і тоді
# сервіс піднімає `services.studio`, а тест — `backend.services.studio`: два
# різні класи з однією назвою, і `pytest.raises` не збігається. Ізольовано
# такий тест проходить, у повному прогоні — падає.
StudioError = sf.studio.StudioError


def test_token_round_trip():
    token = sf._face_token("/Library/Fonts/Brand.ttc", 3)
    path, index = sf._decode_token(token)
    assert path == "/Library/Fonts/Brand.ttc"
    assert index == 3


def test_token_survives_spaces_and_cyrillic():
    """Шляхи з пробілами — норма для macOS («Avenir Next.ttc»)."""
    source = "/System/Library/Fonts/Supplemental/Avenir Next.ttc"
    path, index = sf._decode_token(sf._face_token(source, 0))
    assert (path, index) == (source, 0)


def test_broken_token_is_rejected():
    with pytest.raises(StudioError):
        sf._decode_token("це-не-токен")


def test_import_refuses_path_outside_font_dirs(monkeypatch):
    """Токен приходить із мережі. Навіть у локальній програмі він не має
    відкривати довільний файл у системі."""
    token = sf._face_token("/etc/passwd", 0)
    with pytest.raises(StudioError) as exc:
        sf.import_face(None, token)
    assert "поза теками" in str(exc.value)


def test_import_reports_missing_file(monkeypatch):
    fake_dir = "/tmp/bms-studio-fonts-test"
    os.makedirs(fake_dir, exist_ok=True)
    monkeypatch.setattr(sf, "FONT_DIRS", (fake_dir,))
    token = sf._face_token(os.path.join(fake_dir, "Zник.ttf"), 0)
    with pytest.raises(StudioError) as exc:
        sf.import_face(None, token)
    assert "зник" in str(exc.value).lower()


def test_catalogue_shape():
    """Структура відповіді — контракт для фронта: родини, у кожній накреслення
    з токеном і вагою."""
    data = sf.catalogue()
    assert isinstance(data.get("families"), list)
    for family in data["families"][:5]:
        assert family["family"]
        assert family["source"] in ("user", "system")
        assert family["faces"], f"{family['family']} без накреслень"
        for face in family["faces"]:
            assert face["token"]
            assert 100 <= face["weight"] <= 900
            assert isinstance(face["italic"], bool)


def test_catalogue_puts_user_fonts_first():
    """Свої шрифти цікавлять людину найбільше — вони мають бути вгорі."""
    families = sf.catalogue()["families"]
    sources = [family["source"] for family in families]
    if "user" in sources and "system" in sources:
        assert sources.index("user") < sources.index("system")
