# -*- coding: utf-8 -*-
"""
PyInstaller runtime-hook для BMS.

Виконується ПЕРШИМ при старті фрозен-застосунку, ще до main.py. Завдання —
відтворити dev-розкладку sys.path, щоб бекенд (який бандлиться як ВИХІДНИЙ КОД
у <bundle>/backend) імпортувався так само, як на Mac:

    • <bundle>/            → щоб працювало `import backend.app.main` (uvicorn-рядок)
    • <bundle>/backend     → щоб працювали БARE-імпорти `from models...`,
                              `from services...`, `from routers...`
    • <bundle>/deploy      → щоб працювало `from embedded_db import ...`

У onedir sys._MEIPASS вказує на теку поряд з BMS.exe, де лежать усі дані.
"""

import sys
import os

base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

for sub in ("", "backend", "deploy"):
    p = os.path.join(base, sub) if sub else base
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)
