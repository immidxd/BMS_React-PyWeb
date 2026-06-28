# -*- coding: utf-8 -*-
"""
Embedded PostgreSQL lifecycle manager для автономного вузла (Windows-продакшн).

ЦІЛЬ
────
Зробити BMS повністю автономним: жодного «встанови спершу PostgreSQL вручну».
Інсталятор кладе portable-набір бінарників PostgreSQL 16 поруч із застосунком
(`<app>/postgres/bin/...`), а цей модуль на першому запуску САМ:

    1. initdb       — створює локальний кластер у %LOCALAPPDATA%\\BMS\\pgdata
                      (macOS: ~/Library/Application Support/BMS/pgdata)
    2. конфігурує   — слухає ЛИШЕ 127.0.0.1 (без firewall-діалогу), пароль superuser
    3. start        — піднімає `postgres` як дочірній процес, чекає готовності
    4. ensure db    — створює бойову БД, якщо її ще нема
    5. (опційно)    — відновлює стартовий дамп (cutover з Mac) АБО лишає порожньою,
                      щоб застосунок сам побудував схему через models.database.init_db()

При наступних запусках: бачить готовий кластер → просто start.

ВАЖЛИВО про версії: прод на Mac — EnterpriseDB PostgreSQL 16 (server 16.2).
Windows-бандл МАЄ бути PostgreSQL 16.x, інакше pgdata з іншого major не запуститься.

Цей модуль НЕ залежить від FastAPI/SQLAlchemy — лише stdlib + бінарники pg.
Тому його можна гнати окремо в PoC-раннері й на самому старті main.py.
"""

from __future__ import annotations

import os
import sys
import time
import signal
import shutil
import socket
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("bms.embedded_db")


class EmbeddedPostgresError(RuntimeError):
    pass


def _is_windows() -> bool:
    return os.name == "nt"


def _exe(name: str) -> str:
    """ім'я бінарника з .exe на Windows."""
    return f"{name}.exe" if _is_windows() else name


def default_data_dir() -> Path:
    """Платформозалежна тека для кластера (поза каталогом застосунку — переживає апдейти)."""
    override = os.getenv("BMS_DATA_DIR")
    if override:
        return Path(override).expanduser() / "pgdata"
    if _is_windows():
        base = os.getenv("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
        return Path(base) / "BMS" / "pgdata"
    # macOS / Linux (dev)
    return Path(os.path.expanduser("~/Library/Application Support/BMS")) / "pgdata"


def resolve_pg_bin_dir() -> Path:
    """
    Знайти теку з бінарниками PostgreSQL у такому порядку пріоритету:
      1. BMS_PG_BIN_DIR (явний override — для dev/PoC)
      2. <app_dir>/postgres/bin            (як кладе інсталятор у проді)
      3. відомі системні шляхи (dev-машини): EDB /Library/PostgreSQL/16/bin тощо
      4. PATH (pg_ctl у системі)
    """
    override = os.getenv("BMS_PG_BIN_DIR")
    if override:
        return Path(override)

    # поруч із застосунком (PyInstaller onedir: postgres лежить біля BMS.exe = {app}).
    # ⚠️ НЕ sys._MEIPASS — у PyInstaller 6 onedir це {app}\_internal, а інсталятор
    # (installer.iss) кладе portable PG у {app}\postgres. Тож беремо теку exe.
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parent.parent
    bundled = app_dir / "postgres" / "bin"
    if (bundled / _exe("pg_ctl")).exists():
        return bundled

    # типові системні локації (dev)
    candidates = [
        "/Library/PostgreSQL/16/bin",          # EDB macOS (наш прод-dev)
        "/opt/homebrew/opt/postgresql@16/bin",  # brew macOS
        "/usr/lib/postgresql/16/bin",          # Debian/Ubuntu
        r"C:\Program Files\PostgreSQL\16\bin",  # EDB Windows (системна інсталяція)
    ]
    for c in candidates:
        if (Path(c) / _exe("pg_ctl")).exists():
            return Path(c)

    # останній шанс — PATH
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent

    raise EmbeddedPostgresError(
        "Не знайдено бінарники PostgreSQL. Очікував <app>/postgres/bin або "
        "встанови BMS_PG_BIN_DIR. (Windows-бандл має містити portable PG 16.)"
    )


class EmbeddedPostgres:
    """Керує життєвим циклом локального кластера PostgreSQL."""

    def __init__(
        self,
        *,
        data_dir: Optional[Path] = None,
        bin_dir: Optional[Path] = None,
        port: int = 5432,
        superuser: str = "postgres",
        password: str = "postgres",
        db_name: str = "bsstorage",
        encoding: str = "UTF8",
        locale: str = "C",  # storage collation; дані — UTF8, тож кирилиця ок
    ):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.bin_dir = Path(bin_dir) if bin_dir else resolve_pg_bin_dir()
        self.port = port
        self.superuser = superuser
        self.password = password
        self.db_name = db_name
        self.encoding = encoding
        self.locale = locale
        self._log_path = self.data_dir.parent / "postgres.log"

    # ── шляхи до бінарників ────────────────────────────────────────────────
    def _bin(self, name: str) -> str:
        p = self.bin_dir / _exe(name)
        if not p.exists():
            raise EmbeddedPostgresError(f"Не знайдено {name} у {self.bin_dir}")
        return str(p)

    # ── стан ───────────────────────────────────────────────────────────────
    def is_initialized(self) -> bool:
        return (self.data_dir / "PG_VERSION").exists()

    def is_running(self) -> bool:
        # pg_ctl status: код 0 = працює, 3 = зупинено
        try:
            res = subprocess.run(
                [self._bin("pg_ctl"), "-D", str(self.data_dir), "status"],
                capture_output=True, text=True,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _port_open(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    # ── initdb ───────────────────────────────────────────────────────────────
    def initdb(self) -> None:
        """Створити новий кластер. Пароль superuser — через тимчасовий pwfile."""
        if self.is_initialized():
            logger.info("pgdata вже існує — initdb пропускаю (%s)", self.data_dir)
            return

        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        pwfile = self.data_dir.parent / ".pgpw"
        pwfile.write_text(self.password, encoding="utf-8")
        try:
            logger.info("initdb → %s", self.data_dir)
            cmd = [
                self._bin("initdb"),
                "-D", str(self.data_dir),
                "-U", self.superuser,
                "--pwfile", str(pwfile),
                "-E", self.encoding,
                "--locale", self.locale,
                # local — trust (тільки наш користувач), host TCP — пароль
                "--auth-local=trust",
                "--auth-host=scram-sha-256",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise EmbeddedPostgresError(f"initdb failed:\n{res.stdout}\n{res.stderr}")
        finally:
            try:
                pwfile.unlink()
            except OSError:
                pass

        self._harden_config()
        logger.info("initdb готово")

    def _harden_config(self) -> None:
        """Слухати лише localhost (без firewall-діалогу Windows) + фіксований порт."""
        conf = self.data_dir / "postgresql.conf"
        extra = (
            "\n# ── BMS embedded overrides ──\n"
            "listen_addresses = '127.0.0.1'\n"
            f"port = {self.port}\n"
            "unix_socket_directories = ''\n"  # Windows не має unix-сокетів
        )
        with conf.open("a", encoding="utf-8") as f:
            f.write(extra)

    # ── start / stop ──────────────────────────────────────────────────────────
    def start(self, wait_timeout: float = 30.0) -> None:
        if self.is_running():
            logger.info("PostgreSQL вже працює на :%s", self.port)
            return
        logger.info("Старт PostgreSQL :%s (log → %s)", self.port, self._log_path)
        # pg_ctl start запускає демон і повертається; -w чекає готовності сам,
        # але робимо власний health-loop для надійних логів.
        #
        # ⚠️ Windows-deadlock: демон postgres.exe УСПАДКОВУЄ stdout/stderr від pg_ctl.
        # Якщо ловити їх через PIPE (capture_output=True), subprocess.run чекає EOF
        # цих пайпів — а демон тримає їх відкритими, доки ПРАЦЮЄ → .run() зависає
        # назавжди (pg_ctl давно повернувся, postgres уже "ready", але ми не виходимо).
        # На macOS/Linux демон закриває успадковані fd при відв'язуванні, тож там не
        # відтворюється. Рішення: НЕ створювати пайпи — серверний вивід і так пише сам
        # PostgreSQL у -l logfile, а діагностику дає _log_tail().
        res = subprocess.run(
            [
                self._bin("pg_ctl"),
                "-D", str(self.data_dir),
                "-l", str(self._log_path),
                "-o", f"-p {self.port}",
                "-w", "-t", str(int(wait_timeout)),
                "start",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if res.returncode != 0:
            raise EmbeddedPostgresError(
                f"pg_ctl start failed (rc={res.returncode})\n"
                f"--- postgres.log ---\n{self._log_tail()}"
            )
        # додаткова перевірка через pg_isready
        if not self._wait_ready(wait_timeout):
            raise EmbeddedPostgresError(
                f"PostgreSQL не відповів за {wait_timeout}s\n{self._log_tail()}"
            )
        logger.info("PostgreSQL готовий")

    def _wait_ready(self, timeout: float) -> bool:
        isready = self.bin_dir / _exe("pg_isready")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if isready.exists():
                res = subprocess.run(
                    [str(isready), "-h", "127.0.0.1", "-p", str(self.port)],
                    capture_output=True, text=True,
                )
                if res.returncode == 0:
                    return True
            else:
                if self._port_open():
                    return True
            time.sleep(0.4)
        return False

    def stop(self, mode: str = "fast") -> None:
        if not self.is_initialized() or not self.is_running():
            return
        logger.info("Зупинка PostgreSQL (-m %s)", mode)
        subprocess.run(
            [self._bin("pg_ctl"), "-D", str(self.data_dir), "-m", mode, "-w", "stop"],
            capture_output=True, text=True,
        )

    def _log_tail(self, lines: int = 40) -> str:
        try:
            content = self._log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(content.splitlines()[-lines:])
        except OSError:
            return "(postgres.log недоступний)"

    # ── db / restore ──────────────────────────────────────────────────────────
    def _psql(self, *args: str, db: str = "postgres") -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PGPASSWORD"] = self.password
        return subprocess.run(
            [self._bin("psql"), "-h", "127.0.0.1", "-p", str(self.port),
             "-U", self.superuser, "-d", db, "-v", "ON_ERROR_STOP=1", *args],
            capture_output=True, text=True, env=env,
        )

    def database_exists(self) -> bool:
        res = self._psql(
            "-tAc", f"SELECT 1 FROM pg_database WHERE datname = '{self.db_name}'"
        )
        return res.returncode == 0 and res.stdout.strip() == "1"

    def ensure_database(self) -> bool:
        """Створити бойову БД, якщо її нема. Повертає True, якщо створили щойно."""
        if self.database_exists():
            return False
        logger.info("Створюю базу %s", self.db_name)
        res = self._psql("-c", f'CREATE DATABASE "{self.db_name}" ENCODING \'UTF8\'')
        if res.returncode != 0:
            raise EmbeddedPostgresError(f"CREATE DATABASE failed:\n{res.stderr}")
        return True

    def restore_dump(self, dump_path: Path) -> None:
        """Відновити стартовий дамп (cutover з Mac). Підтримує .sql (plain)."""
        dump_path = Path(dump_path)
        if not dump_path.exists():
            raise EmbeddedPostgresError(f"Дамп не знайдено: {dump_path}")
        logger.info("Відновлення дампу %s → %s", dump_path, self.db_name)
        res = self._psql("-f", str(dump_path), db=self.db_name)
        if res.returncode != 0:
            raise EmbeddedPostgresError(f"restore failed:\n{res.stderr[-2000:]}")
        logger.info("Дамп відновлено")

    def table_count(self) -> int:
        res = self._psql(
            "-tAc",
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='public'",
            db=self.db_name,
        )
        return int(res.stdout.strip() or 0) if res.returncode == 0 else -1
