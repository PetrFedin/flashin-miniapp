import sqlite3

from scripts.check_transaction_integrity import BASE_CHECKS


def _violations(rows):
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE return_requests (order_id INTEGER NOT NULL, status TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO return_requests(order_id, status) VALUES (?, ?)",
            rows,
        )
        return int(connection.execute(BASE_CHECKS["duplicate_open_order_returns"]).fetchone()[0])
    finally:
        connection.close()


def test_multiple_terminal_partial_return_records_are_valid_history():
    assert _violations([
        (1001, "approved_partial"),
        (1001, "approved_partial"),
        (1001, "approved"),
    ]) == 0


def test_one_open_return_plus_terminal_history_is_valid():
    assert _violations([
        (1001, "approved_partial"),
        (1001, "requested"),
    ]) == 0


def test_two_open_returns_for_same_order_are_a_violation():
    assert _violations([
        (1001, "requested"),
        (1001, "refund_pending"),
    ]) == 1


def test_open_returns_on_different_orders_do_not_conflict():
    assert _violations([
        (1001, "processing"),
        (1002, "refund_review_required"),
    ]) == 0


def test_retry_and_review_states_remain_open_for_integrity_purposes():
    assert _violations([
        (1001, "refund_retry_required"),
        (1001, "refund_review_required"),
    ]) == 1


def test_obsolete_all_history_duplicate_check_is_removed():
    assert "duplicate_order_returns" not in BASE_CHECKS
    assert "duplicate_open_order_returns" in BASE_CHECKS
