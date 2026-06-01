"""Нормалізація трек-номерів (ТТН).

Google Sheets зрізає провідний нуль у числових клітинках, тому Укрпошта-ТТН
'0506...' приходять як '506...'. Якщо отримуємо рівно 12 цифр і перша '5'
(Укрпошта-формат) — додаємо нуль на початок. Інші формати (Нова пошта
14 цифр '20...', тощо) повертаємо як є.

Винесено з backend/scripts/orders_pars.py (deprecated parser) у спільні
утиліти, щоб дозволити безпечне видалення старого скрипта згодом.
"""

import re


def normalize_tracking_number(value):
    if not value:
        return value
    s = str(value).strip()
    if not s:
        return value
    if re.fullmatch(r"5\d{11}", s):
        return "0" + s
    return s
