#!/usr/bin/env python3
"""Сторож бюджету Neon, що працює БЕЗ запущеного BMS (launchd / Планувальник завдань).

Навіщо: запобіжник у BMS (services/cloud_budget.py) перевіряє витрату лише поки BMS
відкритий. Каталог 24/7 може будити хмарну БД і вночі — тому цей скрипт раз на 15 хв
робить те саме: читає витрату через Neon API (базу не будить) і на «stop» вимикає
compute. Дозвіл власника («Дозволити ще $1» у BMS) лежить у спільному файлі
~/.bms/cloud_budget_permit.json — сторож його враховує, тож базу назад не вимикає.

Запуск:  python backend/scripts/neon_budget_guard.py   (з кореня BMS)
Автозапуск на Mac: deploy/com.bms.neon-budget-guard.plist
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402

# Той самий порядок, що й у main.py: секрети з теки BMS, потім проєктний .env.
if sys.platform == "win32":
    _cfg = os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"), "BMS")
else:
    _cfg = os.path.expanduser("~/Library/Application Support/BMS")
for _name in ("secrets.env", ".env"):
    if os.path.isfile(os.path.join(_cfg, _name)):
        load_dotenv(os.path.join(_cfg, _name))
        break
load_dotenv(os.path.join(ROOT, ".env"))

from backend.services import cloud_budget  # noqa: E402

if __name__ == "__main__":
    st = cloud_budget.refresh()
    keys = ("level", "capped", "cost_usd", "budget_usd", "projected_usd", "used_cu_hours", "error", "message")
    print(json.dumps({k: st.get(k) for k in keys if st.get(k) is not None}, ensure_ascii=False))
