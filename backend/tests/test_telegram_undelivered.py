"""Виявлення постів, що не доїхали з форуму в канал.

ЧОМУ ЦЕ ІСНУЄ. Канал наповнює сам Telegram: ми віддаємо йому форвард із
`schedule=`, і далі відправка — його справа. Якщо вона провалиться (зникло
вихідне повідомлення форуму, вичерпано ліміт у 100 запланованих, FloodWait), ми
не дізнаємось НІКОЛИ: `telegram_scheduled_posts.state` лишається 'scheduled',
бо його ніхто не оновлює, а черга запланованого в Telegram приватна для того
адміна, який її створив — із сесії програми чужу навіть не видно.

Так 26.08.2026 тихо загубилось 65 товарів.
"""
from __future__ import annotations

import inspect
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backend.services import telegram_publisher as tp  # noqa: E402

SRC = inspect.getsource(tp.undelivered_to_channel)
# ⚠️ Докстрінг ПОЯСНЮЄ, чому не можна зіставляти за номером, і сам згадує
# `product_number_raw`. Перевіряти треба КОД, інакше тест ловить власне
# пояснення (він саме так і зробив при першому прогоні).
BODY = SRC.split('"""', 2)[-1]


def test_matches_by_product_id_never_by_number():
    """⚠️ ГОЛОВНЕ. Зіставляти за номером НЕ МОЖНА.

    `telegram_posts.product_number_raw` тримає номер без префікса ('4229'), а в
    цій базі '#4229' і '#Ф4229' — РІЗНІ товари. Зіставлення за цифрами дає
    впевнено неправильний список: під час розбору воно видало 64 «загублені»
    товари, з яких справжніх була жменя, і підставило чужі пости під чужі
    номери. Правильний шлях один — product_id.
    """
    assert "product_id" in BODY
    assert "product_number_raw" not in BODY, "повернулось зіставлення за номером"
    assert "regexp_replace" not in BODY, "повернулось зіставлення за цифрами"


def test_only_sellable_products_are_reported():
    """Продане й неоцінене доопубліковувати не треба — це був би шум."""
    assert "'Непродано'" in BODY
    assert "p.price, 0) > 0" in BODY or "COALESCE(p.price, 0) > 0" in BODY


def test_status_is_compared_by_name_not_id():
    """Під час розбору я відфільтрував за `statusid = 1`, вважаючи що це
    «Непродано» — і отримав НУЛЬ замість 65. Ідентифікатори статусів тут не
    те, чим здаються; порівнювати треба за назвою."""
    assert "statusid = 1" not in BODY and "statusid=1" not in BODY
    assert "statusname" in BODY


def test_both_chats_come_from_constants():
    """Жодних зашитих id: форум і канал — з констант модуля."""
    assert "FORUM_CHAT_ID" in BODY and "CHANNEL_CHAT_ID" in BODY


def test_endpoint_exists_and_is_wired():
    """Без ендпоінта детектор невидимий, а мовчазна втрата саме цим і страшна."""
    router = (BACKEND / "routers" / "publications.py").read_text(encoding="utf-8")
    assert '"/api/publications/telegram/undelivered"' in router
    assert "_tg_pub().undelivered_to_channel" in router, \
        "прямий доступ до telegram_publisher — у цьому роутері він лінивий"
