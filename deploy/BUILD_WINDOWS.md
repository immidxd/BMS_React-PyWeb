# Складання Windows-інсталятора BMS (Крок D)

> Усі команди — на **Windows 10/11 x64 білд-машині** (або CI). Це єдиний крок,
> який НЕ можна виконати на Mac. Конфіги (`bms.spec`, `installer.iss`,
> `pyi_rthook_bms.py`) уже готові в `deploy/` і приїхали з git.

---

## 0. Передумови (поставити один раз)

| Інструмент | Версія | Звідки |
|---|---|---|
| Python | **3.13.x** (як на dev) | python.org, ✅ «Add to PATH» |
| Node.js | **22.x** | nodejs.org |
| Inno Setup | **6.x** | jrsoftware.org |
| PyInstaller | `pip install pyinstaller` | у venv |
| Portable PostgreSQL | **16.x** Windows binaries | EDB zip (див. §2) |
| WebView2 Runtime bootstrapper | Evergreen Standalone/Bootstrapper | developer.microsoft.com/microsoft-edge/webview2 |

```bat
git clone https://github.com/immidxd/BMS_React-PyWeb.git
cd BMS_React-PyWeb
git checkout feature/windows-autonomous-deploy
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install pyinstaller
```

---

## 1. Зібрати фронтенд

```bat
cd frontend
npm ci
npm run build
cd ..
```
→ оновлює `frontend\build` (його бандлить spec).

---

## 2. Підготувати portable PostgreSQL 16

1. Завантажити **«PostgreSQL Binaries» zip** для Windows x64, версія **16.x**
   (EnterpriseDB → Downloads → «zip archive», НЕ інсталятор).
2. Розпакувати; усередині є `pgsql\` з теками `bin`, `lib`, `share`.
3. Покласти ВМІСТ `pgsql\` у:
   ```
   deploy\staging\postgres\
       ├─ bin\   (postgres.exe, initdb.exe, pg_ctl.exe, psql.exe, pg_isready.exe …)
       ├─ lib\
       └─ share\
   ```
   `embedded_db.resolve_pg_bin_dir()` шукає `<app>\postgres\bin`.

> ⚠️ Major-версія МАЄ бути **16** (прод-дамп зроблено PG 16.2). 15/17 не запустять pgdata з 16.

## 2b. WebView2 bootstrapper

Завантажити `MicrosoftEdgeWebview2Setup.exe` (Evergreen Bootstrapper) і покласти в:
```
deploy\staging\MicrosoftEdgeWebview2Setup.exe
```

---

## 3. Зібрати застосунок (PyInstaller)

```bat
pyinstaller deploy\bms.spec --noconfirm
```
→ `dist\BMS\BMS.exe` + рантайм.

**Перший дебаг:** у `deploy\bms.spec` тимчасово постав `console=True`, щоб бачити
логи/трейсбеки. Запусти `dist\BMS\BMS.exe` напряму — має:
- підняти вбудований PostgreSQL (авто на frozen-Windows),
- на порожній БД побудувати схему (62 табл.) або відновити seed,
- відкрити вікно WebView2 з UI.

Коли працює — поверни `console=False` і перебудуй.

---

## 4. Зібрати інсталятор (Inno Setup)

```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" deploy\installer.iss
```
→ `deploy\Output\BMS_Setup_0.1.0-alpha.exe`

Оновити `#define AppVersion` у `installer.iss` під значення з файлу `VERSION`.

---

## 5. Перший запуск на цільовій машині (cutover)

1. Поставити `BMS_Setup_*.exe` (per-user, без UAC). Інсталятор за потреби
   тихо доставить WebView2 Runtime.
2. Заповнити секрети: створити `%LOCALAPPDATA%\BMS\secrets.env` з
   `deploy\secrets.env.example` (БД-пароль = той, що очікує застосунок; TG/Sheets/R2).
3. **Дані (cutover):** покласти свіжий дамп прод-БД як
   `%LOCALAPPDATA%\BMS\seed.sql` — лаунчер відновить його на першому запуску.
   (Без нього застосунок підніметься з порожньою, але робочою схемою — 62 табл.)
4. **Telegram:** покласти `bms.session` поряд (куди очікує `auth_telegram`) або
   пройти майстер-логін за номером при першому старті.
5. Запустити BMS з ярлика. Перевірити: вікно відкрилось, дані видно,
   діагностичного бейджа НЕМА (бо канал stable).

---

## 6. Типові збої першої збірки (і фікси)

| Симптом | Причина / фікс |
|---|---|
| `ModuleNotFoundError: models` / `services` | runtime-hook не додав шлях — перевір `pyi_rthook_bms.py` і що `backend\` потрапив у бандл (Tree у spec). |
| `ModuleNotFoundError` на 3rd-party (telethon/gspread/…) | додати пакет у список `_COLLECT` у `bms.spec`. |
| `psycopg2` DLL error | взяти `psycopg2-binary` (вже в requirements); перевір, що `collect_all('psycopg2')` спрацював. |
| `pillow_heif` не вантажиться | переконайся, що `collect_all('pillow_heif')` зібрав .dll; HEIC-фото інакше падають (фікс HEIC). |
| Порожнє/біле вікно | WebView2 Runtime не встановлено → перевір крок WebView2; або фронтенд не зібрано (`frontend\build`). |
| initdb падає на locale | у `embedded_db.initdb` вже `--locale C --encoding UTF8`; перевір, що `bin\initdb.exe` з того ж дистрибутиву 16. |
| PG не стартує | дивись `%LOCALAPPDATA%\BMS\postgres.log`. |
| Антивірус / SmartScreen блокує .exe | підписати білд code-signing сертифікатом (пізніше) або «Run anyway» на тесті. |

---

## 7. Що далі (Крок E — оновлення)

Коли інсталятор працює — авто-апдейтер: GitHub Releases + `manifest.json`,
гаряче оновлення `frontend\build`+backend, повний Setup рідко, `pg_dump` бекап
перед апдейтом. Деталі — `WINDOWS_DEPLOY_ROADMAP.md` §4 Крок E.
