from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_payment_route_uses_shared_settlement_service():
    source = (ROOT / "backend" / "api" / "payments.py").read_text(encoding="utf-8")

    assert "from ..services.payment_settlement import" in source
    assert source.count("settle_paid_order(db, order)") == 3
    assert "commit_reservations_to_sold" not in source
    assert "mark_redemption_committed" not in source
    assert "reward_referral_after_first_paid_order" not in source
    assert "ensure_fulfillment_task" not in source


def test_settlement_service_owns_paid_side_effects():
    source = (ROOT / "backend" / "services" / "payment_settlement.py").read_text(
        encoding="utf-8"
    )

    for required in (
        "commit_reservations_to_sold",
        "queue_order_paid",
        "mark_redemption_committed",
        "reward_referral_after_first_paid_order",
        "ensure_fulfillment_task",
        '"order.paid"',
    ):
        assert required in source
