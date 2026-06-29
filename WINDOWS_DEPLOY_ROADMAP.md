# BMS — Дорожня карта автономного Windows-релізу

> Робочий документ для **обережного, послідовного** продовження роботи —
> у т.ч. з іншого компʼютера. Тримати в синхроні з памʼяттю Claude
> (`~/.claude/projects/<project>/memory/project_windows_autonomous_deploy.md`).
>
> **Дата старту:** 2026-06-28 · **Версія застосунку:** див. файл `VERSION`

---

## 0. Головне рішення (не переглядати без причини)

- **Розробка** лишається на **Mac + GitHub**. **Продакшн-рантайм** їде на
  **Windows-машину бізнесу як ЄДИНИЙ продакшн-вузол.** Машина майже завжди онлайн.
- **База даних — локальний PostgreSQL у комплекті** (portable PG **16.x**, бо
  прод-dev = EnterpriseDB PG 16, server 16.2; інший major не запустить pgdata).
- **Сидіння даних = відновлення `pg_dump` з Mac (cutover)**, НЕ генерація з нуля
  (бо `init_db()` поки не будує схему на чисто порожній БД — див. §5).
- Один білд поводиться по-різному за **платформою / каналом / feature-прапорами**
  (`/api/runtime-config`), без розгалуження коду.

---

## 1. Архітектура автономного вузла

```
Windows-машина (прод)
├─ <app>/                         ← PyInstaller onedir (Етап 2)
│  ├─ main.exe                    ← лаунчер (наш main.py)
│  ├─ postgres/bin/*.exe          ← portable PostgreSQL 16 (кладе інсталятор)
│  └─ frontend/build/             ← React-бандл (роздає бекенд)
├─ %LOCALAPPDATA%\BMS\
│  ├─ pgdata/                     ← кластер (initdb на 1-му запуску; переживає апдейти)
│  ├─ postgres.log
│  ├─ secrets.env                 ← ПРОД-секрети (поза інсталятором; див. deploy/secrets.env.example)
│  ├─ config.json                 ← канал + feature-прапори машини (deploy/config.example.json)
│  └─ backups/                    ← pg_dump перед кожним апдейтом
```

Підйом БД робить `deploy/embedded_db.py`; лаунчер вмикає його прапором
`BMS_EMBEDDED_DB=1` у `main.py:start_embedded_db()`.

---

## 2. Що ВЖЕ ЗРОБЛЕНО (Етап 1 + пост) — усе перевірено на Mac

| # | Зроблено | Файли |
|---|----------|-------|
| 1 | Кросплатформенний лаунчер: macOS-чистка кешу під `darwin`; bind-host `BMS_BIND_HOST` (дефолт 127.0.0.1) | `main.py` |
| 2 | Шар runtime-конфігу (platform+channel+flags) + ендпоінт `/api/runtime-config` | `backend/services/runtime_config.py`, `backend/app/main.py` |
| 3 | Файл версії | `VERSION` |
| 4 | Заморожено версії залежностей (`>=`→`==`); прибрано мертвий selenium | `requirements.txt` |
| 5 | Секрети з `%LOCALAPPDATA%\BMS\secrets.env` (пріоритет над .env); dev незмінний | `main.py:setup_environment()` |
| 6 | Менеджер вбудованого PostgreSQL + PoC-раннер | `deploy/embedded_db.py`, `deploy/run_embedded_poc.py` |
| 7 | Інтеграція embedded_db у лаунчер під `BMS_EMBEDDED_DB=1` (+ `BMS_SEED_DUMP` cutover) | `main.py:start_embedded_db()` |
| 8 | Фронтенд читає runtime-config; хуки `useFeatureFlag/usePlatform/useRuntimeConfig` | `frontend/src/contexts/RuntimeConfigContext.tsx`, `index.tsx` |
| 9 | Перший реальний гейт: DevBadge (видно лише dev/beta) | `frontend/src/components/common/DevBadge.tsx`, `App.tsx` |
| 10| Шаблони деплою | `deploy/secrets.env.example`, `deploy/config.example.json` |

**PoC доведено end-to-end (Mac, порт 5433/5434, бойова БД недоторкана):**
`initdb → start → createdb → restore dump → 68 таблиць → clean stop`.

---

## 3. Feature-прапори / канали (як користуватись)

- Канали: `dev` (Mac), `beta`, `stable` (Windows-прод). Визначається:
  `env BMS_CHANNEL` → `config.json` → дефолт (`windows`→stable, інакше dev).
- Прапори: пріоритет `DEFAULT_FLAGS → platform → channel → config.json`.
- **Сценарії:**
  - *Експериментальний білд без шкоди іншим* → окрема гілка + канал beta.
  - *Інший UI під Windows* → `usePlatform()` у фронтенді.
  - *Обмежити функцію на проді, лишити в себе* → `PLATFORM_FLAG_OVERRIDES["windows"]={"X":False}`.
  - *Точково на 1 машині* → `config.json` (найвищий пріоритет, без перезбірки).

---

## 4. НАСТУПНІ КРОКИ (послідовно, обережно)

> Принцип: кожен крок не ламає робочий Mac-застосунок; перевірка після кожного.

### Крок A — git-гігієна (можна з Mac) `[не зроблено]`
- Звести ~20 гілок: `main` = stable. Влити/закрити старі `claude/*`.
- Ця робота — на гілці `feature/windows-autonomous-deploy`.

### Крок B — полагодити `init_db()` для fresh-install (можна з Mac) `[не зроблено]`
- Див. §5. Дає змогу ставити чисту машину без дампу.
- Валідація: `python deploy/run_embedded_poc.py` (без `--restore`) → ≥60 таблиць зелено.

### Крок C — конфіги збірки наперед (можна з Mac) `[✅ ЗРОБЛЕНО 2026-06-29]`
- ✅ `deploy/bms.spec` (PyInstaller onedir): backend/deploy/frontend-build як source-Tree
  (excludes secrets/.session/logs/tests), `collect_all` для важких 3rd-party,
  runtime-hook розширює sys.path.
- ✅ `deploy/pyi_rthook_bms.py` — sys.path (root+backend+deploy) у фрозен-рантаймі.
- ✅ `deploy/installer.iss` (Inno Setup): per-user, postgres staging, WebView2
  bootstrap з реєстр-перевіркою, ярлик/autostart, тека `%LOCALAPPDATA%\BMS`.
- ✅ `deploy/BUILD_WINDOWS.md` — покрокова інструкція + таблиця типових збоїв.
- ✅ main.py: вбудована БД АВТО-вмикається на frozen-Windows (`_embedded_db_enabled()`),
  seed з `BMS_SEED_DUMP` або `<BMS>/seed.sql`; fresh-no-seed → init_db() (62 табл.).
  Перевірено наскрізь на Mac через явний прапор.
- ⚠️ Конфіги НЕ тестовані на Windows — валідація в Кроці D (див. BUILD_WINDOWS.md §6).

### Крок D — складання й перевірка на Windows 10 `[✅ ЗРОБЛЕНО 2026-06-29 на Windows 10]`
- ✅ Python **3.13.14** (python.org, per-user) + venv + `pip install -r requirements.txt` + PyInstaller 6.21.
  Node НЕ ставили — перевикористали наявний `frontend/build` (статика, платформо-незалежна).
- ✅ Portable **PostgreSQL 16.10** (EDB zip) → `deploy/staging/postgres/` (trim: без pgAdmin/StackBuilder/doc).
  WebView2 Evergreen bootstrapper → `deploy/staging/`; Runtime на машині присутній.
- ✅ `pyinstaller deploy/bms.spec` → `dist/BMS/BMS.exe` (265 МБ onedir, console=False).
- ✅ `ISCC deploy/installer.iss` → `deploy/Output/BMS_Setup_0.1.0-alpha.exe` (~108 МБ).
- ✅ **Наскрізний запуск перевірено:** BMS.exe → вбудований PG 16.10 (initdb→start) →
  restore seed (68 таблиць, 11 404 товари) → бекенд :8000 (HTTP 200) → вікно WebView2,
  фронтенд опитує API. Авто-вмикання embedded DB на frozen-Windows працює.

**🐞 Дві Windows-only вади, знайдені й виправлені (коміт `cd3e85601`, `deploy/embedded_db.py`):**
1. `resolve_pg_bin_dir()` брав `sys._MEIPASS` (= `{app}\_internal` у PyInstaller 6 onedir),
   а `installer.iss` кладе PG у `{app}\postgres`. Тепер коли frozen → тека `sys.executable`.
2. `EmbeddedPostgres.start()`: `pg_ctl start` спавнить демон, що успадковує stdout/stderr;
   `capture_output=True` → `subprocess.run` вічно чекає EOF пайпів (демон тримає їх) = deadlock.
   Прибрано PIPE (серверний вивід і так у `-l` logfile). На macOS не відтворювалось.

**➕ Доповнено requirements (коміт `1a2e27f32`):** `google-api-python-client`, `pycountry` —
бекенд їх імпортує (`product_images_drive.py`, `googlesheets_pars.py`), але в requirements
їх бракувало → у frozen-білді впали б `ModuleNotFoundError`.

**✅ Реліз 0.1.1-alpha (2026-06-29, Mac) — закриває heel_types-проблему + WS:**
- `main.py`: в embedded-режимі ПІСЛЯ restore теж викликається `init_db()` —
  доганяє схему старішого дампу (idempotent). Більше не залежимо від «свіжості» дампу.
- `database.py`: ALTER `clients.phone_number TYPE varchar(255)` зроблено ідемпотентним
  (лише коли вужче за 255) — інакше падав на відновленому дампі через тригер
  `trg_clients_sync_to_contacts` і відкочував увесь блок init_db.
- `requirements.txt`: `websockets==16.0` — лікує WinError 10054, вмикає live прогрес-бар.
- Перевірено на Mac: fresh-install 62 табл.; restore СТАРОГО дампу + init_db → догори
  (24/24 міграції, heel_types+brand_blocklist присутні, 77 табл.).
- ⚠️ Потребує релізного РЕБІЛДУ на Windows (PyInstaller+ISCC) щоб websockets потрапив у бандл.
- Свіжий `pg_dump` з Mac (§6.1) усе одно бажаний для актуальних ДАНИХ (не лише схеми).
- Нефатально: backfill `*_normalized` може пропускатись на дублях FB-URL (під guard).

### Крок E — авто-апдейтер

**E1 — ВИЯВЛЕННЯ оновлень `[✅ ЗРОБЛЕНО 2026-06-29, Mac]`**
- `backend/services/updater.py` — тягне `manifest.json` за `BMS_UPDATE_MANIFEST_URL`,
  порівнює версію каналу з локальною (packaging). Read-only, стійке до офлайну.
- `/api/update-status` — ендпоінт статусу (enabled/update_available/latest/url/sha256/notes).
- `frontend/.../UpdateBanner.tsx` — ненав'язливий банер «доступне оновлення» (dismissible),
  показується лише коли є новіша; інакше null. Підключено в App.tsx.
- `deploy/make_manifest.py` — генератор manifest.json (sha256+size+url) для релізу, з `--merge`.
- Перевірено на Mac (file:// маніфест): newer→True, рівна→False, без URL→enabled:false.
- Маніфест публікувати за стабільним URL (GitHub Releases asset / raw / R2);
  виставити `BMS_UPDATE_MANIFEST_URL` у secrets.env прод-машини.

**E2 — ЗАСТОСУВАННЯ оновлень `[не зроблено; ризикова Windows-частина]`**
- Гаряче оновлення (часто): завантажити, звірити sha256, замінити `frontend/build` +
  backend-код, рестарт процесу.
- Повний `Setup.exe` (рідко): коли змінились бінарні залежності/PG.
- **Перед будь-яким апдейтом — `pg_dump` у `%LOCALAPPDATA%\BMS\backups`.**
- Канали: тестуєш на Mac (dev) → beta → stable (Windows).

---

## 5. ⚠️ Відомий баг: `init_db()` не будує схему з нуля

`backend/models/database.py::init_db()` робить `create_all()` (лише SQLAlchemy-
моделі) і далі inline-SQL `INSERT INTO brand_blocklist ...` — але `brand_blocklist`
**не модель**, create_all її не створює → на порожній БД падає
`UndefinedTable: relation "brand_blocklist" does not exist`.

- **Зараз оминаємо** через cutover-дамп (дамп містить усі 68 таблиць).
- **Фікс (Крок B):** знайти, де реально створюється `brand_blocklist`
  (ймовірно `backend/migrations/*.sql`), додати `CREATE TABLE IF NOT EXISTS`
  ДО першого звернення в init_db; перевірити інші не-модельні таблиці в тому ж блоці.

---

## 6. Операційний CUTOVER (одноразово, при переїзді на Windows)

1. **Свіжий дамп прод-БД з Mac:**
   ```
   /Library/PostgreSQL/16/bin/pg_dump -h 127.0.0.1 -p 5432 -U postgres \
     -d bsstorage -f bms_cutover_YYYYMMDD.sql
   ```
2. **Telegram-сесія:** скопіювати `backend/bms.session` (інакше — майстер-логін за
   номером на Windows при першому запуску).
3. **Google Sheets креди:** `mcp-google-sheets/working_credentials.json`
   (або `GOOGLE_SHEETS_JSON_KEY` у secrets.env).
4. **Секрети:** заповнити `%LOCALAPPDATA%\BMS\secrets.env` з `deploy/secrets.env.example`.
5. Перший запуск → `BMS_SEED_DUMP=<дамп>` відновить дані у вбудований PG.

---

## 7. Що КОПІЮВАТИ на інший компʼютер

> Код їде через git. Решта — **поза git** (секрети/дані/контекст), переносити вручну.

**A. Код (через git):** ця гілка `feature/windows-autonomous-deploy` запушена в
`github.com/immidxd/BMS_React-PyWeb`. На іншій машині: `git clone` + `git checkout`.

**B. Контекст для Claude (щоб новий чат «памʼятав» план):**
скопіювати ВСЮ теку памʼяті
`~/.claude/projects/-Users-i-malashenko-Desktop-react-fastapi-app/memory/`
у відповідну `~/.claude/...` на іншій машині (особливо `MEMORY.md` +
`project_windows_autonomous_deploy.md`). ⚠️ Історію самого чату це НЕ відновить —
лише законспектовані факти. Цей `WINDOWS_DEPLOY_ROADMAP.md` уже їде з git.

**C. Секрети й дані (НЕ в git, нести вручну/безпечним каналом):**
- `.env` (або зібраний `secrets.env`) — БД/Telegram/Sheets/R2/OLX ключі.
- `db_cutover_*.sql` — свіжий дамп прод-БД (для seed).
- `backend/bms.session` — Telegram-сесія.
- `mcp-google-sheets/working_credentials.json` — Google service-account.

⚠️ Секрети й `.session` **ніколи не комітити** в git.

---

## 8. Як перевірити локально (Mac)

```
# PoC автономного вузла (cutover-шлях):
python deploy/run_embedded_poc.py --restore db_backup_20260602_041452.sql

# PoC fresh-install (впаде, поки §5 не пофікшено):
python deploy/run_embedded_poc.py

# Тайпчек + білд фронтенду:
cd frontend && npx tsc --noEmit && npm run build
```

---

## 9. Інваріанти безпеки (не порушувати)

- Жодних змін у `main`, поки не злито свідомо.
- Бойову БД на :5432 не чіпати тестами (PoC — окремі порти 5433/5434, тимч. теки).
- На Mac без прапорів (`BMS_EMBEDDED_DB`, `BMS_CHANNEL`) — поведінка незмінна.
- Один білд фронтенду після `tsc --noEmit` (не плодити CRA-білди).
- Перед апдейтом прод-машини — завжди `pg_dump` бекап.
