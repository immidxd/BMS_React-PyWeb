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

### Крок C — конфіги збірки наперед (можна з Mac) `[не зроблено]`
- `deploy/bms.spec` (PyInstaller onedir): включити `backend`, `frontend/build`,
  `deploy/embedded_db.py`, моделі/роутери/сервіси, `.session`-логіку, hidden-imports
  (psycopg2, telethon, gspread, PIL, pillow_heif).
- `deploy/installer.iss` (Inno Setup): файли застосунку, `postgres/bin`,
  bootstrap **WebView2 Runtime**, ярлик, (опц.) autostart.
- Інструкція складання бандла: де взяти portable PG 16 (EDB zip), WebView2.

### Крок D — складання й перевірка на Windows 10 `[ПОТРЕБУЄ Windows]`
- Поставити Python 3.13 + Node 22 на Windows-білд-машину (або CI).
- `pip install -r requirements.txt`, `npm ci && npm run build` (у `frontend`).
- PyInstaller за `bms.spec` → Inno Setup за `installer.iss` → `BMS_Setup_x.y.z.exe`.
- Перший запуск з `BMS_EMBEDDED_DB=1` + `BMS_SEED_DUMP=<дамп>`.
- Перевірити: вікно WebView2 відкривається, дані з дампу видно, бейджа НЕМА (stable).

### Крок E — авто-апдейтер `[після D]`
- GitHub Releases + `manifest.json` (версія/канал/хеш/URL).
- Гаряче оновлення (часто): заміна `frontend/build` + backend-коду, рестарт.
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
