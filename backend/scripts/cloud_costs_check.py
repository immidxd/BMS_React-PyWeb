#!/usr/bin/env python3
"""Перевірка «Сервери й хмара» з терміналу: опитує провайдерів і друкує стан кожного
сервісу БЕЗ секретів (ключі не виводяться). Запуск з кореня BMS:

    ./venv/bin/python backend/scripts/cloud_costs_check.py
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from backend.services import cloud_budget, cloud_costs  # noqa: E402

if __name__ == "__main__":
    cloud_budget.refresh()
    st = cloud_costs.refresh()
    for s in st.get("services") or []:
        usage = ", ".join(f"{u['label']}={u['value']}{(' ' + u['unit']) if u.get('unit') else ''}" for u in s.get("usage") or [])
        print(f"{s['key']:20} {s['status']:15} ${s['cost_usd']:<7} {usage}")
        if s.get("error"):
            print(f"{'':20} ПОМИЛКА: {s['error'].splitlines()[0][:200]}")
    print(json.dumps({"total": st.get("total"),
                      "areas": {a["name"]: a["cost_usd"] for a in st.get("areas") or []},
                      "usd_uah": st.get("usd_uah")}, ensure_ascii=False))
