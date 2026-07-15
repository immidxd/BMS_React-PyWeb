from types import SimpleNamespace

from backend.utils.order_parser_logic import fill_missing_order_client


def test_fills_only_a_missing_order_client():
    order = SimpleNamespace(client_id=None)

    assert fill_missing_order_client(order, 48665) is True
    assert order.client_id == 48665


def test_never_replaces_an_existing_order_client():
    order = SimpleNamespace(client_id=123)

    assert fill_missing_order_client(order, 456) is False
    assert order.client_id == 123


def test_does_not_clear_an_existing_or_missing_client():
    linked = SimpleNamespace(client_id=123)
    anonymous = SimpleNamespace(client_id=None)

    assert fill_missing_order_client(linked, None) is False
    assert fill_missing_order_client(anonymous, None) is False
    assert linked.client_id == 123
    assert anonymous.client_id is None
