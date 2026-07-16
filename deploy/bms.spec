# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec для автономного Windows-білда BMS (onedir).

ЗАПУСК (на Windows-білд-машині, з кореня репо):
    pyinstaller deploy/bms.spec --noconfirm

РЕЗУЛЬТАТ:
    dist/BMS/BMS.exe  + поряд уся рантайм-бібліотека (onedir).
    Далі цю теку пакує Inno Setup (deploy/installer.iss) у Setup.exe,
    додаючи portable PostgreSQL та WebView2 Runtime.

СТРАТЕГІЯ ІМПОРТІВ (важливо):
    Бекенд має змішані імпорти (`from models...` працює лише коли backend/ на
    sys.path). Тому ми НЕ покладаємось на статичний аналіз PyInstaller для backend,
    а БАНДЛИМО backend/ як вихідний код (datas) і розширюємо sys.path у рантаймі
    (runtime-hook pyi_rthook_bms.py). Третьосторонні залежності, які backend тягне
    в рантаймі (psycopg2, telethon, …), збираємо явно через collect_all нижче —
    інакше їх не буде у фрозен-бандлі, бо аналізатор їх «не бачить» крізь source.

⚠️ Цей spec — БЕЗ можливості тестування на macOS. Перша збірка на Windows майже
напевно потребує дрібних правок (див. deploy/BUILD_WINDOWS.md → «Типові збої»).
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_submodules
from PyInstaller.building.datastruct import Tree

# Корінь репо: spec лежить у deploy/, тож піднімаємось на рівень.
REPO_ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH інжектить PyInstaller)

block_cipher = None

# ── Третьосторонні пакети, які треба зібрати ПОВНІСТЮ (binaries+datas+submodules),
#    бо backend завантажується як source і аналізатор їх не простежує ──────────────
_COLLECT = [
    "psycopg2",        # native драйвер PostgreSQL
    "telethon",        # Telegram (+ rsa/pyaes крипто)
    "gspread",
    "google",          # google-auth namespace
    "googleapiclient",
    "google_auth_oauthlib",
    "oauth2client",
    "PIL",             # Pillow
    "pillow_heif",     # HEIC (iPhone-фото)
    "pydantic",
    "pydantic_core",
    "sqlalchemy",
    "uvicorn",
    "fastapi",
    "starlette",
    "apscheduler",
    "pycountry",       # містить data-файли (ISO-довідники)
    "dotenv",
    "requests",
    "certifi",
    "boto3",           # Cloudflare R2 client (lazy import in r2_storage)
    "botocore",        # boto3 runtime + service models/config
    "anyio",
    "bs4",             # beautifulsoup4
    "lxml",
]

datas, binaries, hiddenimports = [], [], []
for pkg in _COLLECT:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # noqa: BLE001
        print(f"[bms.spec] collect_all({pkg}) пропущено: {e}")

# uvicorn вантажить воркери/протоколи рядком — додаємо підмодулі явно.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "psycopg2", "psycopg2._psycopg",
    "email.mime.text", "email.mime.multipart",  # часом потрібні
]

# ── Дані застосунку (бандлимо як файли; рантайм-розкладка дзеркалить dev) ─────────
# Tree автоматично рекурсує; excludes ЗАХИЩАЄ від потрапляння секретів/сміття.
backend_tree = Tree(
    os.path.join(REPO_ROOT, "backend"),
    prefix="backend",
    excludes=[
        "__pycache__", "*.pyc", "*.pyo",
        "*.log", "app.log",
        "*.session", "bms.session",      # ⚠️ Telegram-сесія — НІКОЛИ не в інсталятор
        "tests", "manual_cleanup_backups",
        "unknown_errors.txt",
    ],
)
deploy_tree = Tree(
    os.path.join(REPO_ROOT, "deploy"),
    prefix="deploy",
    excludes=["__pycache__", "*.pyc", "secrets.env", "config.json"],
)
frontend_build_tree = Tree(
    os.path.join(REPO_ROOT, "frontend", "build"),
    prefix="frontend/build",
    excludes=["__pycache__"],
)

datas += [
    (os.path.join(REPO_ROOT, "VERSION"), "."),
]

a = Analysis(
    [os.path.join(REPO_ROOT, "main.py")],
    pathex=[REPO_ROOT, os.path.join(REPO_ROOT, "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[os.path.join(REPO_ROOT, "deploy", "pyi_rthook_bms.py")],
    excludes=[
        "selenium", "webdriver_manager",  # мертві (вже прибрані з requirements)
        "tkinter", "pytest", "matplotlib",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Долучаємо source-trees ПІСЛЯ Analysis (вони не аналізуються, лише копіюються).
a.datas += backend_tree
a.datas += deploy_tree
a.datas += frontend_build_tree

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BMS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # ⚠️ для першого дебагу постав True (видно логи/трейсбеки)
    disable_windowed_traceback=False,
    icon=None,              # TODO: deploy/bms.ico, коли буде
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="BMS",
)
