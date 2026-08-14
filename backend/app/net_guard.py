"""Захист від «мертвого» IPv6, який вішає весь бекенд.

**Що сталось 2026-08-14 (реальний інцидент, 3 години простою).** Ноутбук був у
роздачі з iPhone (`172.20.10.x`). Хотспот РЕКЛАМУЄ IPv6 — у системи є глобальна
адреса `2a02:…` — але фактично IPv6-трафік не маршрутизується. `getaddrinfo`
повертає AAAA першою, Python чесно йде в IPv6 і зависає в `connect()`:

    IPv6 → sheets.googleapis.com : таймаут (відповіді немає взагалі)
    IPv4 → sheets.googleapis.com : 0.8 с, HTTP 404 (нормальна відповідь)

`socket.connect()` без таймауту не має власного дедлайну — сокет лишається в
`SYN_SENT` фактично назавжди. У процесі BMS таких висіли 4 штуки. Наслідок був
не «повільна синхронізація», а повна смерть застосунку: не відповідали навіть
статика `/` і `/docs`, парсинг-джоб застряг на `initializing` і три години
висів у БД як `running`.

**Чому це не разова випадковість.** На цій мережі воно відтворюється щоразу при
старті. На звичайному Wi-Fi (робочий IPv6 або взагалі без нього) — не
відтворюється ніколи. Саме тому симптом виглядав «плаваючим».

**Що робить цей модуль.** На старті один раз пробує підняти IPv6-з'єднання з
коротким дедлайном. Якщо IPv6 мертвий — прибирає AAAA з результатів
``getaddrinfo``, і всі вихідні з'єднання (Google Sheets/Drive, Telegram, R2,
диспетчери) одразу йдуть по IPv4. Плюс ставить дефолтний таймаут на створення
сокетів, щоб «немає відповіді» перетворювалось на помилку, а не на вічне
очікування.

Перевірка повторюється при кожному старті, тож щойно мережа стане нормальною,
IPv6 повернеться сам — нічого вмикати руками не треба.
"""

import os
import socket
import logging

logger = logging.getLogger(__name__)

# Куди стукати для перевірки. Саме Google API — головна залежність, яка вішала
# парсинг; порт 443, бо саме туди йде увесь робочий трафік.
_PROBE_HOST = os.getenv("NET_GUARD_PROBE_HOST", "sheets.googleapis.com")
_PROBE_PORT = int(os.getenv("NET_GUARD_PROBE_PORT", "443"))

# Скільки чекати на IPv6, перш ніж визнати його мертвим. Тримаємо мало: це ціна
# старту застосунку на кожному запуску.
_PROBE_TIMEOUT = float(os.getenv("NET_GUARD_PROBE_TIMEOUT", "1.5"))

# Дефолтний таймаут для сокетів, створених БЕЗ явного таймауту. Без нього
# бібліотеки без власного дедлайну зависають назавжди.
_DEFAULT_SOCKET_TIMEOUT = float(os.getenv("NET_GUARD_SOCKET_TIMEOUT", "20"))

_original_getaddrinfo = socket.getaddrinfo
_ipv4_forced = False


def _ipv6_is_usable() -> bool:
    """Чи справді працює IPv6, а не лише «є адреса»."""
    try:
        infos = _original_getaddrinfo(_PROBE_HOST, _PROBE_PORT, socket.AF_INET6, socket.SOCK_STREAM)
    except socket.gaierror:
        return False  # AAAA немає — нічого й форсувати, стек і так піде в IPv4
    if not infos:
        return False

    family, socktype, proto, _canon, sockaddr = infos[0]
    probe = socket.socket(family, socktype, proto)
    try:
        probe.settimeout(_PROBE_TIMEOUT)
        probe.connect(sockaddr)
        return True
    except (socket.timeout, OSError):
        return False
    finally:
        probe.close()


def _force_ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """``getaddrinfo`` без AAAA — щоб жодна бібліотека не пішла в мертвий IPv6."""
    if family == socket.AF_INET6:
        # Явний запит саме IPv6 не підміняємо: хай отримає чесну помилку.
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    results = _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    return results


def apply_network_guards() -> dict:
    """Викликати ОДИН раз на старті, до будь-яких мережевих операцій."""
    global _ipv4_forced

    if socket.getdefaulttimeout() is None:
        socket.setdefaulttimeout(_DEFAULT_SOCKET_TIMEOUT)

    if os.getenv("NET_GUARD", "1") == "0":
        logger.info("[net-guard] вимкнено через NET_GUARD=0")
        return {"enabled": False, "ipv4_forced": False}

    if _ipv4_forced:
        return {"enabled": True, "ipv4_forced": True, "already": True}

    usable = _ipv6_is_usable()
    if usable:
        logger.info("[net-guard] IPv6 робочий — залишаємо як є")
        return {"enabled": True, "ipv4_forced": False, "ipv6_usable": True}

    socket.getaddrinfo = _force_ipv4_getaddrinfo
    _ipv4_forced = True
    logger.warning(
        "[net-guard] ⚠️ IPv6 не відповідає (%s:%s за %.1f с) — примусово переходимо на IPv4. "
        "Типова причина: роздача з телефона, яка рекламує IPv6, але не маршрутизує його. "
        "Без цього вихідні з'єднання зависають у SYN_SENT і вішають бекенд.",
        _PROBE_HOST, _PROBE_PORT, _PROBE_TIMEOUT,
    )
    return {"enabled": True, "ipv4_forced": True, "ipv6_usable": False}


def network_guard_status() -> dict:
    """Для діагностики з UI: у якому режимі працює мережа."""
    return {
        "ipv4_forced": _ipv4_forced,
        "default_socket_timeout": socket.getdefaulttimeout(),
        "probe_host": _PROBE_HOST,
    }
