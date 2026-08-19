import logging
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import sys
import os
# Ensure both backend package and project root are on sys.path for absolute imports
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT_DIR = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.append(BACKEND_DIR)
if PROJECT_ROOT_DIR not in sys.path:
    sys.path.append(PROJECT_ROOT_DIR)

from models.database import engine, Base, init_db
from routers import (
    products,
    clients,
    orders,
    payment_statuses,
    order_statuses,
    delivery_methods,
    parsing,
    search,
)
from routers import journal_sync as journal_sync_router
try:
    from routers import deliveries  # optional
except Exception:
    deliveries = None
try:
    from routers import suppliers  # optional
except Exception:
    suppliers = None
try:
    from routers import shipments  # optional
except Exception:
    shipments = None
try:
    from routers import statistics  # optional
except Exception:
    statistics = None
try:
    from routers import brands  # optional
except Exception:
    brands = None
try:
    from routers import publications  # optional — Telegram/social media publications
except Exception:
    publications = None
try:
    from routers import merge_candidates  # optional — workspace merge UX
except Exception:
    merge_candidates = None
try:
    from routers import content_plan  # optional — контент-план з Obsidian TaskNotes
except Exception:
    content_plan = None

# НАЛАШТУВАННЯ ЛОГУВАННЯ
# Використовуємо абсолютний шлях і гарантуємо наявність директорії,
# щоб запуск не ламався, коли CWD відрізняється (наприклад, у PyWebView)
LOG_DIR = os.path.join(PROJECT_ROOT_DIR, 'backend', 'app')
LOG_FILE = os.path.join(LOG_DIR, 'app.log')
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    pass

# Рівень логів керується env LOG_LEVEL (default INFO).
# ⚠️ DEBUG глобально = тормоза на старті: telethon пише ~10 рядків на КОЖНЕ
# повідомлення Telegram, і все синхронно ллється в консоль+файл одночасно —
# ця I/O-лавина блокує GIL якраз у вікні запуску (симптом: «висить після старту»).
# Тому шумні бібліотеки явно приглушені до WARNING незалежно від LOG_LEVEL.
_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(
    level=_LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding='utf-8')
    ]
)

# Заглушити надмірно балакучі сторонні логери (мережевий шум, не наші помилки).
for _noisy in (
    "telethon",
    "telethon.network.mtprotosender",
    "telethon.extensions.messagepacker",
    "googleapiclient.discovery",
    "google_auth_httplib2",
    "google.auth.transport.requests",
    "urllib3.connectionpool",
    "urllib3.util.retry",
    "asyncio",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Ensure Google Sheets creds are available to parsers (single source of truth).
# Шлях через спільний резолвер: %LOCALAPPDATA%\BMS\working_credentials.json (прод)
# → mcp-google-sheets/ (dev-Mac). Виставлений env успадкують і парсер-subprocess'и.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if not os.getenv('GOOGLE_SHEETS_CREDENTIALS_FILE'):
    try:
        from services.runtime_config import credentials_file as _sheets_creds
    except ImportError:
        from backend.services.runtime_config import credentials_file as _sheets_creds
    _sheets_key = _sheets_creds()
    if _sheets_key:
        os.environ['GOOGLE_SHEETS_CREDENTIALS_FILE'] = _sheets_key
        logger.info(f"GOOGLE_SHEETS_CREDENTIALS_FILE set to {_sheets_key}")

# Database initialization moved to separate script for faster startup
logger.info("Database connection ready")

# ⚠️ ДО будь-яких мережевих операцій: якщо мережа рекламує IPv6, але не
# маршрутизує його (класика — роздача з iPhone), кожне вихідне з'єднання
# зависає в SYN_SENT назавжди і вішає ВЕСЬ бекенд, включно зі статикою.
# Перевірено на інциденті 2026-08-14: 3 години простою. Деталі — у net_guard.py.
try:
    from app.net_guard import apply_network_guards
except ImportError:
    from backend.app.net_guard import apply_network_guards
apply_network_guards()

app = FastAPI()

# Add CORS middleware with explicit origins
allowed_origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8000",
]
logger.info(f"Setting up CORS middleware with origins: {allowed_origins}")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prevent caching of API responses + index.html (PyWebView кешує index.html → 404 після нового білду)
@app.middleware("http")
async def no_cache_api(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    is_api = path.startswith("/api/")
    is_index = path in ("/", "/index.html") or (not path.startswith("/static/") and "." not in path.split("/")[-1])
    if is_api or is_index:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception handler caught: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

# Health check endpoint
@app.get("/api/health")
async def health_check():
    logger.debug("Health check endpoint called")
    return {"status": "ok"}

# Runtime-конфіг: платформа + канал + feature-прапори. Фронтенд читає це на старті
# й рендериться відповідно (різний UI під Windows / обмеження функцій по платформі/
# каналу). Read-only, без побічних ефектів — лише читає опційний config.json.
@app.get("/api/runtime-config")
async def runtime_config():
    try:
        from services.runtime_config import get_runtime_config
    except ImportError:
        from backend.services.runtime_config import get_runtime_config
    return get_runtime_config()

# Статус оновлення (Крок E1): чи є новіша версія в каналі цієї машини.
# Read-only — тягне manifest.json за BMS_UPDATE_MANIFEST_URL і порівнює версії.
# Нічого не завантажує/не застосовує. Без URL → {"enabled": false}.
@app.get("/api/update-status")
async def update_status():
    try:
        from services.updater import check_for_update
    except ImportError:
        from backend.services.updater import check_for_update
    return check_for_update()

# Include routers directly (no prefix needed as routes are now fully specified)
app.include_router(products.router)
app.include_router(clients.router, tags=["clients"])  # routes define full /api prefix
app.include_router(orders.router, tags=["orders"])   # routes define full /api prefix
app.include_router(payment_statuses.router, tags=["payment-statuses"])  # routes define full /api prefix
app.include_router(order_statuses.router, tags=["order-statuses"])      # routes define full /api prefix
app.include_router(delivery_methods.router, tags=["delivery-methods"])  # routes define full /api prefix
app.include_router(parsing.router, prefix="/api/parsing", tags=["parsing"])  # router exposes paths like /parsing/.. → final /api/parsing/..
app.include_router(search.router, tags=["search"])  # search router already has /api/search prefix
app.include_router(journal_sync_router.router, tags=["journal-sync"])  # /api/journal-sync/..
if suppliers:
    app.include_router(suppliers.router, tags=["suppliers"])  # routes already prefixed with /api
if deliveries:
    app.include_router(deliveries.router, tags=["deliveries"])  # routes already prefixed with /api
if shipments:
    app.include_router(shipments.router, tags=["shipments"])  # routes already prefixed with /api
if statistics:
    app.include_router(statistics.router, tags=["statistics"])  # routes already prefixed with /api
if brands:
    app.include_router(brands.router, tags=["brands"])  # routes already prefixed with /api
if publications:
    app.include_router(publications.router, tags=["publications"])  # routes already prefixed with /api
if merge_candidates:
    app.include_router(merge_candidates.router, tags=["merge-candidates"])  # routes already prefixed with /api
if content_plan:
    app.include_router(content_plan.router, tags=["content-plan"])  # routes already prefixed with /api

# Mount product images directory (local + Google Drive overlay; abstraction in services/product_images.py)
try:
    from services.product_images import get_images_dir, URL_PREFIX as IMG_URL_PREFIX
except ImportError:
    from backend.services.product_images import get_images_dir, URL_PREFIX as IMG_URL_PREFIX
_images_dir = get_images_dir()
if os.path.isdir(_images_dir):
    logger.info(f"Mounting LOCAL product images from {_images_dir} → {IMG_URL_PREFIX}")
    app.mount(IMG_URL_PREFIX, StaticFiles(directory=_images_dir), name="product-images")
else:
    logger.info(f"Local product images dir not found ({_images_dir}) — using Drive only")

# Drive image proxy: streams bytes from Google Drive with disk-cache
try:
    from services.product_images_drive import (
        get_drive_file_bytes, get_drive_index_stats, invalidate_drive_index,
        URL_PREFIX_DRIVE,
    )
except ImportError:
    try:
        from backend.services.product_images_drive import (
            get_drive_file_bytes, get_drive_index_stats, invalidate_drive_index,
            URL_PREFIX_DRIVE,
        )
    except ImportError:
        get_drive_file_bytes = None
        URL_PREFIX_DRIVE = "/product-images-drive"

if get_drive_file_bytes is not None:
    from fastapi import Response, HTTPException

    @app.get(URL_PREFIX_DRIVE + "/{file_id}")
    def stream_drive_image(file_id: str):
        """Proxy: GET image bytes from Google Drive (cached on disk).

        ⚠️ `def`, а НЕ `async def` — і це критично. `get_drive_file_bytes` повністю
        синхронний: диск-кеш + завантаження через googleapiclient з таймаутом 60 с.
        У корутині він виконувався просто на event loop uvicorn'а, тобто ОДНЕ
        некешоване фото морозило ВЕСЬ бекенд на весь час качання — запит наступної
        картки товару стояв у черзі за ним. Це давало «зависла інформація про
        попередній товар». `def` → FastAPI виносить у threadpool, фото качаються
        паралельно й нікому не заважають.
        """
        result = get_drive_file_bytes(file_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Image not found in Drive")
        data, mime = result
        return Response(
            content=data, media_type=mime,
            # file_id у Drive незмінний і вміст за ним не міняється → immutable.
            # Рік замість доби: браузер більше не переперевіряє фото щодня.
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.post("/api/product-images-drive/refresh")
    def refresh_drive_index():
        """Manually rebuild Drive index (e.g. after uploading new photos)."""
        invalidate_drive_index()
        return {"ok": True, "stats": get_drive_index_stats()}

    @app.get("/api/product-images-drive/stats")
    def drive_index_stats():
        return get_drive_index_stats()

    logger.info(f"Drive image proxy mounted: {URL_PREFIX_DRIVE}/<file_id>")


# ── Мініатюри фото ────────────────────────────────────────────────────────────
# Стрічка прев'ю, плитки менеджера і швидкий перегляд показують фото 64–320 px,
# а тягнули оригінал (локально ≈100 КБ, з Drive ≈800 КБ). Ці роути віддають
# WebP-мініатюру з диск-кешу. Будь-який збій генерації → віддаємо оригінал:
# мініатюри прискорюють, але ніколи не стають причиною «фото зникло».
# Обидва роути — `def` (threadpool): decode+resize блокуючий.
try:
    from services.product_thumbs import (
        thumb_for_local, thumb_for_drive, normalize_width, cache_stats as _thumb_stats,
    )
except ImportError:
    try:
        from backend.services.product_thumbs import (
            thumb_for_local, thumb_for_drive, normalize_width, cache_stats as _thumb_stats,
        )
    except ImportError:
        thumb_for_local = None

if thumb_for_local is not None:
    from fastapi import Response as _Response, HTTPException as _HTTPException, Query as _Query
    from fastapi.responses import FileResponse as _FileResponse

    # Рік + immutable: URL мініатюри несе `?v=` оригіналу (mtime+size), тож при
    # заміні фото змінюється сам URL — кеш браузера не треба «просити» оновитись.
    _THUMB_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}

    @app.get("/product-images-thumb/{relpath:path}")
    def product_image_thumb(relpath: str, w: int = _Query(320, ge=16, le=2048)):
        """Мініатюра локального фото. `relpath` — той самий шлях, що й у
        /product-images/<relpath> (включно з категорійною підпапкою)."""
        base = os.path.realpath(_images_dir)
        target = os.path.realpath(os.path.join(base, relpath))
        # Захист від виходу за корінь фото (`..` у шляху).
        if not target.startswith(base + os.sep) or not os.path.isfile(target):
            raise _HTTPException(status_code=404, detail="Фото не знайдено")
        data = thumb_for_local(target, normalize_width(w))
        if data is None:
            return _FileResponse(target, headers=_THUMB_HEADERS)
        return _Response(content=data, media_type="image/webp", headers=_THUMB_HEADERS)

    @app.get("/product-images-drive-thumb/{file_id}")
    def product_image_drive_thumb(file_id: str, w: int = _Query(320, ge=16, le=2048)):
        """Мініатюра фото з Drive (оригінал бере з байтового кешу Drive-провайдера)."""
        data = thumb_for_drive(file_id, normalize_width(w))
        if data is not None:
            return _Response(content=data, media_type="image/webp", headers=_THUMB_HEADERS)
        # Фолбек — оригінал через той самий шлях, що й основний проксі.
        if get_drive_file_bytes is None:
            raise _HTTPException(status_code=404, detail="Фото не знайдено")
        result = get_drive_file_bytes(file_id)
        if result is None:
            raise _HTTPException(status_code=404, detail="Фото не знайдено")
        return _Response(content=result[0], media_type=result[1], headers=_THUMB_HEADERS)

    @app.get("/api/product-thumbs/stats")
    def product_thumbs_stats():
        count, size = _thumb_stats()
        return {"files": count, "bytes": size, "mb": round(size / 1e6, 1)}

    logger.info("Product thumbnail routes mounted: /product-images-thumb, /product-images-drive-thumb")

    @app.on_event("startup")
    async def _prewarm_drive_index():
        """Прогріти Drive-індекс фото у фоні на старті, щоб перша відкрита
        картка не чекала повний скан Drive синхронно."""
        try:
            try:
                from services.product_images_drive import prewarm_drive_index
            except ImportError:
                from backend.services.product_images_drive import prewarm_drive_index
            prewarm_drive_index()
            logger.info("Drive image index prewarm triggered (background)")
        except Exception as e:
            logger.warning(f"Drive index prewarm failed: {e}")

# Mount static files from frontend build if available
frontend_build_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../frontend/build"))


class SPAStaticFiles(StaticFiles):
    """StaticFiles, що віддає index.html (і будь-який HTML) з no-cache.

    Чому: CRA-білд хешує імена JS/CSS-чанків (їх можна кешувати вічно), АЛЕ
    index.html посилається на ці хеші й має оновлюватись щобілда. Без
    Cache-Control браузер/PyWebView застосовує евристичне кешування і тримає
    старий index.html → старий бандл (симптом: зміни в UI «не застосовуються»).
    no-cache змушує ревалідувати index.html щоразу; хешовані ассети лишаються
    кешованими (immutable).
    """
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        ctype = response.headers.get("content-type", "")
        if "text/html" in ctype:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        elif "/static/" in (scope.get("path") or ""):
            # Хешовані ассети — безпечно кешувати надовго
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if os.path.exists(frontend_build_dir):
    logger.info(f"Mounting static files from {frontend_build_dir}")
    app.mount("/", SPAStaticFiles(directory=frontend_build_dir, html=True))
else:
    logger.warning("Frontend build directory not found, static files will not be served")

# ── Auto-startup parse ────────────────────────────────────────────────────────
# При кожному запуску backend автоматично запускаємо "Швидкий повний парсинг
# (товар+замовлення)" у фоновому режимі. Користувач не бачить цього в UI
# (немає jobId у frontend стейті). Якщо користувач вручну запустить парсинг —
# auto-job скасується кооперативно (пріоритет manual).
@app.on_event("startup")
async def _reap_stale_parse_jobs():
    """Помітити «зомбі»-джоби (status running/queued без heartbeat) як failed.
    Killed-процес лишає parsing_jobs у 'running' назавжди → per-card sync вічно
    пропускається (anti-conflict guard), і ручні рядки журналу не зʼявляються."""
    try:
        from sqlalchemy import text as _text
        try:
            from models.database import SessionLocal as _SL
        except ImportError:
            from backend.models.database import SessionLocal as _SL
        db = _SL()
        try:
            res = db.execute(_text(
                """UPDATE parsing_jobs
                   SET status='failed',
                       error_summary=COALESCE(error_summary,'') || ' [reaped: stale on startup]',
                       ended_at=NOW()
                   WHERE status IN ('queued','running')
                     AND COALESCE(last_heartbeat_at, updated_at, started_at) < NOW() - INTERVAL '120 seconds'"""
            ))
            db.commit()
            if res.rowcount:
                logger.warning(f"Reaped {res.rowcount} stale parse job(s) on startup")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Stale-job reaper failed: {e}")


@app.on_event("startup")
async def _journal_sync_worker():
    """Драйнер черги записів у журнал.

    Дві ролі: на старті добрати те, що лишилось незаписаним після минулого
    сеансу (падіння/перезапуск під час фонового запису), і далі періодично
    підбирати задачі, яким настав час повторної спроби — інакше відкладена з
    відступом задача чекала б наступної правки, щоб хтось її розбудив.
    """
    import asyncio
    try:
        from services import journal_sync as _js
    except ImportError:
        from backend.services import journal_sync as _js

    async def _loop():
        await asyncio.sleep(20)   # дати старту застосунку відпрацювати
        while True:
            try:
                await asyncio.to_thread(_js.drain)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"journal-sync drain failed: {e}")
            await asyncio.sleep(60)

    asyncio.create_task(_loop())


@app.on_event("startup")
async def _auto_startup_parse():
    import asyncio

    async def _delayed_start():
        # Затримка, щоб UI встиг завантажитись і став інтерактивним ДО старту
        # важкого фонового парсингу (інакше «тормозить відразу після запуску»:
        # парсинг + Drive-прогрів + Telegram-синк конкурують за GIL і пул БД).
        # Налаштовується через AUTO_PARSE_DELAY_SEC (0 = вимкнути старт-парсинг —
        # journal-poller усе одно підхопить зміни через ~90с).
        import os as _os
        _delay = int(_os.getenv("AUTO_PARSE_DELAY_SEC", "45"))
        if _delay <= 0:
            logger.info("Auto-parse on startup disabled (AUTO_PARSE_DELAY_SEC=0)")
            return
        await asyncio.sleep(_delay)
        try:
            from routers.parsing import start_auto_full_quick
            start_auto_full_quick()
        except Exception as e:
            logger.warning(f"Auto-parse startup failed: {e}")

    asyncio.create_task(_delayed_start())


# ── Journal change poller (near-real-time sheet→DB sync) ──────────────────────
# Раз на JOURNAL_POLL_SEC перевіряє modifiedTime (lastUpdateTime) журналу та
# замовлень. Якщо змінилось із минулого разу — запускає auto quick-parse (правки/
# додавання в аркуші підхоплюються за ~хвилину без рестарту). Сам modifiedTime-чек
# дешевий → не плодимо skip-джоби. Відкл: JOURNAL_POLLER=0.
@app.on_event("startup")
async def _journal_change_poller():
    import asyncio
    import os as _os
    if _os.getenv("JOURNAL_POLLER", "1") == "0":
        return
    poll_sec = int(_os.getenv("JOURNAL_POLL_SEC", "90"))

    # Кулдаун між АВТО-парсингами. Кожен парс перечитує всі аркуші, а Google має
    # ліміт «Read requests per minute per user». Поки власник активно заповнює
    # журнал, кожна правка міняла lastUpdateTime → парс запускався щоцикл (заміряно
    # 22:18→23:40: ~22 парси поспіль, кожні ~3 хв). Квота вигоряла, і РУЧНИЙ повний
    # парсинг падав з 429. Кулдаун коалесить серію правок в один парс: зміни не
    # губляться (lastUpdateTime лишається новим → парс піде після кулдауну).
    cooldown_sec = int(_os.getenv("AUTO_PARSE_COOLDOWN_SEC", "900"))

    async def _loop():
        import time as _time
        await asyncio.sleep(35)  # дати startup auto-parse відпрацювати першим
        last: dict = {}
        pending = False
        last_auto_at = 0.0
        while True:
            try:
                try:
                    from scripts.sheets_parser import get_gc, JOURNAL_ID, ORDERS_ID
                    from routers.parsing import start_auto_full_quick
                except ImportError:
                    from backend.scripts.sheets_parser import get_gc, JOURNAL_ID, ORDERS_ID
                    from backend.routers.parsing import start_auto_full_quick
                gc = get_gc()
                changed = False
                for sid in (JOURNAL_ID, ORDERS_ID):
                    try:
                        lut = gc.open_by_key(sid).lastUpdateTime
                    except Exception:
                        continue
                    if sid in last and last[sid] != lut:
                        changed = True
                    last[sid] = lut
                if changed:
                    pending = True
                if pending:
                    waited = _time.monotonic() - last_auto_at
                    if waited < cooldown_sec:
                        logger.info(
                            "Journal-poller: зміни є, чекаю кулдаун (%ds з %ds)",
                            int(waited), cooldown_sec)
                    else:
                        logger.info("Journal-poller: зміну виявлено → auto quick-parse")
                        if start_auto_full_quick() is not None:
                            # Запустився (не скіпнутий через активний job) — знімаємо
                            # прапорець і починаємо новий відлік кулдауну.
                            pending = False
                            last_auto_at = _time.monotonic()
            except Exception as e:
                logger.warning(f"Journal-poller error: {e}")
            await asyncio.sleep(poll_sec)

    asyncio.create_task(_loop())


# ── Auto-sync publications (Telegram) ─────────────────────────────────────────
# Single sync cycle: scan channels → relink → recovery.
# Triggered on startup (after 15s delay) and periodically every PERIOD seconds.
# Failures are logged and do not crash the process; next cycle retries.
async def _publications_sync_cycle() -> None:
    import os
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE")
    if not all([api_id, api_hash, phone]):
        logger.info("Publications-sync skipped: TG creds not set")
        return

    try:
        from services.telegram_service import TelegramScanner
        from models.database import SessionLocal
    except ImportError:
        from backend.services.telegram_service import TelegramScanner
        from backend.models.database import SessionLocal
    from sqlalchemy import text as _sql_text

    scanner = TelegramScanner(api_id=int(api_id), api_hash=api_hash, phone=phone)
    if not await scanner.connect():
        logger.warning("Publications-sync: TG connect failed — will retry next cycle")
        return

    db = SessionLocal()
    try:
        totals = {"posts_scanned": 0, "new_posts_saved": 0}
        for chat_id, info in TelegramScanner.KNOWN_CHANNELS.items():
            try:
                res = await scanner.scan_channel(db, str(chat_id), info["type"])
                if isinstance(res, dict) and "error" not in res:
                    totals["posts_scanned"] += int(res.get("posts_scanned", 0) or 0)
                    totals["new_posts_saved"] += int(res.get("new_posts_saved", 0) or 0)
                else:
                    logger.warning(f"Pub-sync: channel {chat_id} returned {res}")
            except Exception as ce:
                logger.warning(f"Pub-sync: channel {chat_id} failed: {ce}")
        logger.info(f"Pub-sync: {totals}")

        # Auto-relink
        try:
            try:
                from routers.publications import _RELINK_SQL
            except ImportError:
                from backend.routers.publications import _RELINK_SQL
            r = db.execute(_sql_text(_RELINK_SQL))
            db.commit()
            logger.info(f"Pub-relink: {r.rowcount} posts relinked")
        except Exception as re:
            logger.warning(f"Pub-relink failed: {re}")
            db.rollback()

        # Recovery — phantom archives back to published
        stats = await scanner.verify_archived_posts(db, limit=1000)
        logger.info(f"Pub-recovery: {stats}")
    except Exception as e:
        logger.warning(f"Publications-sync inner failed: {e}")
    finally:
        db.close()
        try:
            await scanner.disconnect()
        except Exception:
            pass


# ── Auto-sync OLX adverts ─────────────────────────────────────────────────────
# Незалежний від Telegram (TG може бути не налаштований). No-op якщо OLX не
# сконфігуровано/не авторизовано. Блокуючі HTTP-запити OLX — у thread executor,
# щоб не стопорити event loop. refresh-токен оновлюється всередині sync_adverts.
async def _olx_sync_cycle() -> None:
    import asyncio

    def _run():
        try:
            from services import olx_service
            from models.database import SessionLocal
            from routers.publications import _RELINK_OLX_SQL
        except ImportError:
            from backend.services import olx_service
            from backend.models.database import SessionLocal
            from backend.routers.publications import _RELINK_OLX_SQL
        from sqlalchemy import text as _sql_text
        if not olx_service.is_configured():
            return
        db = SessionLocal()
        try:
            res = olx_service.sync_adverts(db)
            if res.get("ok"):
                try:
                    r = db.execute(_sql_text(_RELINK_OLX_SQL))
                    db.commit()
                    logger.info(f"OLX-sync: {res} relinked={r.rowcount}")
                except Exception as re:
                    db.rollback()
                    logger.warning(f"OLX-relink failed: {re}")
            else:
                logger.info(f"OLX-sync skipped: {res.get('error')}")
        finally:
            db.close()

    try:
        await asyncio.get_event_loop().run_in_executor(None, _run)
    except Exception as e:
        logger.warning(f"OLX-sync failed: {e}")


# Prom read-sync (товари + замовлення) у фоні. ЛИШЕ читання — пуш наявності на
# Prom лишається РУЧНИМ (рішення власника). Токен статичний → без OAuth-refresh.
async def _prom_sync_cycle() -> None:
    import asyncio

    def _run():
        try:
            from services import prom_service, shafa_service
            from models.database import SessionLocal
        except ImportError:
            from backend.services import prom_service, shafa_service
            from backend.models.database import SessionLocal
        db = SessionLocal()
        try:
            if not prom_service.is_authorized(db):
                return
            rp = prom_service.sync_products(db)
            ro = prom_service.sync_orders(db)
            # Надійно довести експортовані товари до статусу «чернетка»
            # (створення на Prom асинхронне — черга ретраїть щоциклу).
            try:
                dq = prom_service.process_draft_queue(db)
            except Exception as _e:
                dq = {"error": str(_e)}
            # Офіційний міст глобальний. Після кожного Prom-read-sync автоматично
            # віддзеркалюємо локально: pending -> waiting_prom, живий+наявний ->
            # bridge_ready. Фактичним Shafa оголошенням це не прикидається.
            try:
                sr = shafa_service.reconcile_expected_from_prom(db)
            except Exception as _e:
                db.rollback()
                sr = {"error": str(_e)}
            # Чесна верифікація: публічно (без токенів) звіряємо ВЖЕ ВІДОМІ
            # Shafa-оголошення — тримаємо confirmed сам і синхронізуємо реальну
            # наявність з боку Shafa. Автовиявлення нових тут не робимо.
            try:
                from services import shafa_reader
            except ImportError:
                from backend.services import shafa_reader
            try:
                svr = shafa_reader.reconcile_confirmed(db)
            except Exception as _e:
                db.rollback()
                svr = {"error": str(_e)}
            # monoБазар: лише READ-верифікація публічним API вітрини продавця
            # (без токенів). Постинг заблокований — тут тільки моніторинг.
            try:
                from services import monobazar_reader
            except ImportError:
                from backend.services import monobazar_reader
            try:
                mbr = (monobazar_reader.sync_listings(db)
                      if monobazar_reader.get_seller_username(db) else {"skipped": "no username"})
            except Exception as _e:
                db.rollback()
                mbr = {"error": str(_e)}
            logger.info(
                f"Prom-sync: products={rp} orders={ro} drafts={dq} "
                f"shafa={sr} shafa_verify={svr} monobazar={mbr}")
        finally:
            db.close()

    try:
        await asyncio.get_event_loop().run_in_executor(None, _run)
    except Exception as e:
        logger.warning(f"Prom-sync failed: {e}")


@app.on_event("startup")
async def _auto_startup_publications_refresh():
    """Initial sync at startup + periodic loop every N seconds."""
    import asyncio
    import os

    # Configurable via env (default 30 minutes)
    period_sec = int(os.getenv("PUBLICATIONS_SYNC_PERIOD_SEC", "1800"))

    async def _run_all_cycles():
        # Telegram + OLX — кожен у власному try, щоб збій одного не блокував інший.
        # Prom — В ОКРЕМОМУ, ЧАСТІШОМУ циклі нижче (читання дешеве, немає вебхуків).
        for label, fn in (("Publications", _publications_sync_cycle), ("OLX", _olx_sync_cycle)):
            try:
                await fn()
            except Exception as e:
                logger.warning(f"{label}-sync cycle failed: {e}")

    async def _initial_then_periodic():
        # Initial run: wait 15s for the rest of the app to come up
        await asyncio.sleep(15)
        await _run_all_cycles()
        # Periodic loop
        while True:
            await asyncio.sleep(period_sec)
            await _run_all_cycles()

    # Prom-читання (замовлення+товари) — власний ЧАСТІШИЙ цикл (default 10 хв),
    # бо в Prom немає вебхуків, а опитування дешеве. Наявність НА Prom пушиться
    # окремо (тригер після парсингу, з тротлом ~1/год). PROM_SYNC_PERIOD_SEC=0 — вимкнути.
    prom_period = int(os.getenv("PROM_SYNC_PERIOD_SEC", "600"))

    async def _prom_periodic():
        if prom_period <= 0:
            return
        await asyncio.sleep(25)  # трохи пізніше за старт-парсинг
        while True:
            try:
                await _prom_sync_cycle()
            except Exception as e:
                logger.warning(f"Prom-sync cycle failed: {e}")
            await asyncio.sleep(prom_period)

    asyncio.create_task(_initial_then_periodic())
    asyncio.create_task(_prom_periodic())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
