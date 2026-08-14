"""Захист від мертвого IPv6 (інцидент 2026-08-14, 3 години простою).

Мережа рекламувала IPv6, але не маршрутизувала його; `connect()` без таймауту
зависав у SYN_SENT назавжди і вішав увесь бекенд разом зі статикою.
"""

import socket

import pytest

from backend.app import net_guard


@pytest.fixture(autouse=True)
def _restore_socket_state():
    """Тест не має лишати підмінений getaddrinfo наступним тестам."""
    original_getaddrinfo = socket.getaddrinfo
    original_timeout = socket.getdefaulttimeout()
    original_forced = net_guard._ipv4_forced
    yield
    socket.getaddrinfo = original_getaddrinfo
    socket.setdefaulttimeout(original_timeout)
    net_guard._ipv4_forced = original_forced


def test_working_ipv6_is_left_alone(monkeypatch):
    monkeypatch.setattr(net_guard, "_ipv6_is_usable", lambda: True)
    net_guard._ipv4_forced = False

    result = net_guard.apply_network_guards()

    assert result["ipv4_forced"] is False
    assert socket.getaddrinfo is not net_guard._force_ipv4_getaddrinfo


def test_dead_ipv6_forces_ipv4(monkeypatch):
    monkeypatch.setattr(net_guard, "_ipv6_is_usable", lambda: False)
    net_guard._ipv4_forced = False

    result = net_guard.apply_network_guards()

    assert result["ipv4_forced"] is True
    assert socket.getaddrinfo is net_guard._force_ipv4_getaddrinfo


def test_forced_resolver_asks_only_for_ipv4(monkeypatch):
    seen = {}

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        seen["family"] = family
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.1.1", 443))]

    monkeypatch.setattr(net_guard, "_original_getaddrinfo", fake_getaddrinfo)

    net_guard._force_ipv4_getaddrinfo("sheets.googleapis.com", 443)

    assert seen["family"] == socket.AF_INET


def test_explicit_ipv6_request_is_not_silently_rewritten(monkeypatch):
    """Явний запит AF_INET6 має отримати чесну помилку, а не тихий IPv4."""
    seen = {}

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        seen["family"] = family
        return []

    monkeypatch.setattr(net_guard, "_original_getaddrinfo", fake_getaddrinfo)

    net_guard._force_ipv4_getaddrinfo("example.com", 443, socket.AF_INET6)

    assert seen["family"] == socket.AF_INET6


def test_guard_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("NET_GUARD", "0")
    monkeypatch.setattr(net_guard, "_ipv6_is_usable",
                        lambda: pytest.fail("проба не має виконуватись при NET_GUARD=0"))
    net_guard._ipv4_forced = False

    result = net_guard.apply_network_guards()

    assert result == {"enabled": False, "ipv4_forced": False}


def test_default_socket_timeout_is_set(monkeypatch):
    """Без дефолтного таймауту «немає відповіді» = вічне очікування."""
    monkeypatch.setattr(net_guard, "_ipv6_is_usable", lambda: True)
    socket.setdefaulttimeout(None)
    net_guard._ipv4_forced = False

    net_guard.apply_network_guards()

    assert socket.getdefaulttimeout() is not None


def test_probe_treats_timeout_as_dead_ipv6(monkeypatch):
    """Саме таймаут, а не відмова, був симптомом інциденту."""
    class _HangingSocket:
        def settimeout(self, _): pass
        def connect(self, _): raise socket.timeout("timed out")
        def close(self): pass

    monkeypatch.setattr(net_guard, "_original_getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET6, socket.SOCK_STREAM, 6, "",
                                          ("2001:4860:4844:400::", 443, 0, 0))])
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _HangingSocket())

    assert net_guard._ipv6_is_usable() is False


def test_missing_aaaa_record_is_not_treated_as_broken_network(monkeypatch):
    """Немає AAAA — стек і так піде в IPv4, форсувати нічого не треба."""
    def _no_aaaa(*a, **k):
        raise socket.gaierror("no AAAA")

    monkeypatch.setattr(net_guard, "_original_getaddrinfo", _no_aaaa)

    assert net_guard._ipv6_is_usable() is False
