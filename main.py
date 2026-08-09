#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
import threading
import time
import uvicorn
import webview
from dotenv import load_dotenv
import socket
import http.client

# Configure logging
# Рівень керується env LOG_LEVEL (default INFO). DEBUG глобально душить старт —
# див. коментар у backend/app/main.py. Шумні бібліотеки приглушуються там.
_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def _bms_config_dir():
    """Тека з користувацькою конфігурацією/секретами (поза каталогом застосунку,
    переживає апдейти). Та сама логіка, що в backend/services/runtime_config.py."""
    override = os.getenv("BMS_DATA_DIR")
    if override:
        return os.path.expanduser(override)
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return os.path.join(base, "BMS")
    return os.path.expanduser("~/Library/Application Support/BMS")

def setup_environment():
    """Завантажити середовище у порядку пріоритету (перше виграє, бо override=False):
       1. %LOCALAPPDATA%\\BMS\\secrets.env або .env  — ПРОД-секрети (поза інсталятором,
          переживають оновлення; не лежать поряд з exe відкритим текстом)
       2. проєктний .env                              — DEV-fallback (твій Mac)
    На Mac файлів із (1) немає → вантажиться лише проєктний .env, як і раніше.
    """
    cfg = _bms_config_dir()
    for name in ("secrets.env", ".env"):
        p = os.path.join(cfg, name)
        if os.path.isfile(p):
            load_dotenv(p)
            logger.info(f"Loaded production secrets from {p}")
            break
    # проєктний .env (dev) — заповнює те, що ще не задане
    load_dotenv()
    
def start_backend():
    """Start the FastAPI backend server"""
    try:
        # Десктоп-режим: слухаємо лише локальний інтерфейс — Windows не показує
        # діалог фаєрволу, і порт не світиться у LAN. Якщо колись потрібен доступ
        # з інших пристроїв — виставити BMS_BIND_HOST=0.0.0.0 у середовищі.
        host = os.getenv("BMS_BIND_HOST", "127.0.0.1")
        uvicorn.run("backend.app.main:app", host=host, port=8000, log_level="info")
    except Exception as e:
        logger.error(f"Failed to start backend server: {e}")
        sys.exit(1)

def wait_for_backend(max_retries=240, delay=0.5):
    """Wait for backend server to become available.

    Uses 127.0.0.1 explicitly (not 'localhost') to avoid macOS IPv6 resolution
    issues where localhost → ::1 but uvicorn listens on 127.0.0.1 only.

    max_retries=240 (≈120с): стартові події бекенда (Telegram pub-sync + парсинг,
    подеколи з повторами через SSL) блокують відповідь /api/health на ~60-90с.
    15с (старе значення) здавалося завчасно й гасило daemon-бекенд, який інакше
    піднявся б. Збільшено, щоб лаунчер дочекався. (Прискорення старту — окремо:
    винести блокуючу синхронізацію у фон, щоб /api/health відповідав одразу.)
    """
    for i in range(max_retries):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=2)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            conn.close()
            logger.info(f"Backend is available! (HTTP {resp.status})")
            return True
        except Exception as e:
            logger.debug(f"Health check failed: {type(e).__name__}: {e}")
        logger.info(f"Waiting for backend to start (attempt {i+1}/{max_retries})...")
        time.sleep(delay)
    logger.error("Backend failed to start within expected time")
    return False

def _embedded_db_enabled():
    """Чи піднімати вбудований PostgreSQL.

    Пріоритет:
      1. Явний BMS_EMBEDDED_DB (1/true/yes | 0/false/no) — завжди виграє.
      2. Інакше АВТО: увімкнено лише для frozen-білда (PyInstaller) на Windows —
         там СУБД у комплекті і це і є автономний прод. На dev-Mac/Linux або при
         запуску з вихідників — вимкнено (системний PostgreSQL, як і раніше).
    Завдяки авто інсталятору не треба виставляти змінні середовища.
    """
    explicit = os.getenv("BMS_EMBEDDED_DB", "").lower()
    if explicit in ("1", "true", "yes"):
        return True
    if explicit in ("0", "false", "no"):
        return False
    return bool(getattr(sys, "frozen", False)) and sys.platform.startswith("win")

def start_embedded_db():
    """Автономний режим: підняти ВБУДОВАНИЙ PostgreSQL ПЕРЕД бекендом.

    Вмикається за _embedded_db_enabled() (явний прапор або авто на frozen-Windows).
    Параметри з env (їх завантажив setup_environment): DB_PORT/DB_USER/DB_PASSWORD/
    DB_NAME. На першому запуску (свіжий кластер) відновлює seed-дамп: спершу з
    BMS_SEED_DUMP, інакше з <BMS-теки>/seed.sql (куди його кладе інсталятор/cutover).
    Якщо seed немає — init_db() побудує порожню схему сам (fresh-install, Крок B).
    """
    if not _embedded_db_enabled():
        return None

    deploy_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy")
    if deploy_dir not in sys.path:
        sys.path.insert(0, deploy_dir)
    from embedded_db import EmbeddedPostgres  # noqa: E402

    pg = EmbeddedPostgres(
        port=int(os.getenv("DB_PORT", "5432")),
        superuser=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", "postgres"),
        db_name=os.getenv("DB_NAME", "bsstorage"),
    )
    fresh = not pg.is_initialized()
    pg.initdb()       # no-op, якщо кластер уже існує
    pg.start()
    created = pg.ensure_database()

    if fresh or created:
        # seed: явний BMS_SEED_DUMP → інакше стандартне <BMS-теки>/seed.sql
        seed = os.getenv("BMS_SEED_DUMP")
        if not (seed and os.path.isfile(seed)):
            default_seed = os.path.join(_bms_config_dir(), "seed.sql")
            seed = default_seed if os.path.isfile(default_seed) else None
        if seed:
            logger.info(f"Перший запуск: відновлюю стартовий дамп {seed}")
            pg.restore_dump(seed)

        # ЗАВЖДИ синхронізуємо схему до актуальної через init_db() (idempotent):
        #   • no-seed  → будує схему з нуля (fresh-install, Крок B);
        #   • із seed  → ДОГАНЯЄ старіший дамп — додає таблиці/колонки, яких у ньому
        #     бракує (напр. heel_types з пізнішої міграції), інакше /api/products → 500.
        # Безпечно на повній БД: populate_initial_data() пропускає вже наявні довідники,
        # ALTER/міграції — IF NOT EXISTS, backfill-и під guard. Бекенд init_db на старті
        # не кличе, тож робимо це тут, поки БД у нашому контролі.
        logger.info("Синхронізую схему до актуальної (init_db)…")
        backend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
        for p in (os.path.dirname(os.path.abspath(__file__)), backend_dir):
            if p not in sys.path:
                sys.path.insert(0, p)
        try:
            from models.database import init_db  # noqa: E402
            init_db()
            logger.info("Схема актуальна")
        except Exception as e:  # noqa: BLE001
            logger.error(f"init_db() при синхронізації схеми впав: {e}")

    import atexit
    atexit.register(pg.stop)  # коректна зупинка при виході
    logger.info("Вбудований PostgreSQL готовий (:%s)", pg.port)
    return pg

def main():
    """
    Main entry point for the application.
    Starts the FastAPI backend and loads the React frontend in a PyWebView window.
    """
    setup_environment()

    # Позначаємо, що бекенд працює всередині десктоп-застосунку (PyWebView).
    # Бекенд віддає цей прапор у /api/runtime-config, а фронтенд за ним обирає
    # спосіб ЗБЕРЕЖЕННЯ ФАЙЛІВ: у вбудованому вебв'ю `<a download>` не працює
    # (WKWebView просто відкриває blob-URL — фото розгортається на весь екран і
    # застосунок стає непридатним), тож там зберігає бекенд, який на тій самій
    # машині. Ставимо ДО старту бекенд-потоку — інакше він прочитає старе env.
    os.environ["BMS_DESKTOP"] = "1"

    # Автономний режим (Windows-прод): підняти вбудований PostgreSQL ДО бекенда,
    # бо бекенд конектиться до БД одразу на старті. На Mac без прапора — no-op.
    start_embedded_db()

    # Start the backend server in a separate thread
    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()
    
    # Wait for backend to become available
    if not wait_for_backend():
        logger.error("Exiting due to backend unavailability")
        sys.exit(1)
    
    # Get frontend URL (always use the backend's static file server in production).
    # Додаємо унікальний ?v=<час старту> — WKWebView не зможе віддати закешований
    # index.html для нової (унікальної) URL, тож завжди вантажиться свіжий бандл.
    # SPA на "/" віддає index.html незалежно від query, тож параметр безпечний.
    frontend_url = f"http://localhost:8000/?v={int(time.time())}"
    logger.info(f"Connecting to frontend at {frontend_url}")

    # ── КРИТИЧНО: чистимо HTTP-кеш WKWebView на диску ДО старту вікна ──────────
    # WKWebView евристично кешує index.html у ~/Library/Caches/org.python.python/WebKit
    # і цей кеш переживає рестарти процесу. Через це новий білд «не застосовується»
    # (старий index.html → старі JS-чанки → старий UI). caches.delete() (Cache
    # Storage API) це НЕ чистить. Видаляємо HTTP-кеш на рівні файлів.
    # localStorage (налаштування колонок) лежить в ІНШІЙ теці
    # (~/Library/WebKit/org.python.python/.../LocalStorage) — її НЕ чіпаємо.
    def clear_http_cache_on_disk():
        import shutil
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, "Library/Caches/org.python.python/WebKit"),
            os.path.join(home, "Library/Caches/com.apple.python/WebKit"),
        ]
        for path in candidates:
            if os.path.isdir(path):
                try:
                    shutil.rmtree(path)
                    logger.info(f"Cleared WKWebView HTTP cache: {path}")
                except Exception as e:
                    logger.warning(f"Could not clear cache {path}: {e}")

    # Лише macOS: ці шляхи й сам WKWebView існують тільки тут. На Windows
    # PyWebView використовує Edge WebView2 (інший движок, інше сховище кешу), і
    # свіжість бандла там забезпечує cache-bust ?v=<час> у frontend_url вище.
    if sys.platform == "darwin":
        clear_http_cache_on_disk()

    # Очистити Cache Storage API (доповнює дискову чистку вище)
    def clear_webview_cache(window):
        try:
            window.evaluate_js(
                "if(window.caches){caches.keys().then(ks=>ks.forEach(k=>caches.delete(k)));}"
            )
        except Exception:
            pass

    # Create the window
    logger.info("Starting PyWebView window")
    window = webview.create_window(
        "Product and Order Management System",
        frontend_url,
        width=1200,
        height=800,
        min_size=(800, 600)
    )

    # Start the PyWebView application
    webview.start(clear_webview_cache, window, debug=False)

if __name__ == "__main__":
    main()
    