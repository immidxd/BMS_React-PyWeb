from backend.utils.order_status_logic import (
    STATUS_QUEUED,
    is_anonymous_queue_marker,
    real_order_sql,
)


def test_only_anonymous_queued_row_is_marker():
    assert is_anonymous_queue_marker("", STATUS_QUEUED)
    assert is_anonymous_queue_marker("   ", STATUS_QUEUED)
    assert not is_anonymous_queue_marker("Олена", STATUS_QUEUED)
    assert not is_anonymous_queue_marker("", 1)
    assert not is_anonymous_queue_marker(None, None)


def test_real_order_sql_preserves_named_queue_orders():
    predicate = real_order_sql("ord")
    assert "COALESCE(ord.order_status_id, 0) = 8" in predicate
    assert "ord.client_id IS NULL" in predicate
    assert predicate.startswith("NOT (")
