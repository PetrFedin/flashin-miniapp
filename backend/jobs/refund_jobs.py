from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Order, ReturnRequest
from ..services.payments import fetch_yookassa_refund
from ..services.refund_state import (
    apply_provider_refund_status,
    provider_refund_amount,
    refund_money,
)

_PENDING_STATUSES = {
    "processing",
    "refund_pending",
    "refund_retry_required",
    "refund_review_required",
}
_FINAL_STATUSES = {"approved", "approved_partial", "failed"}


async def reconcile_pending_refunds(db: Session, limit: int = 50) -> dict[str, int]:
    candidate_ids = [
        row[0]
        for row in (
            db.query(ReturnRequest.id)
            .filter(
                ReturnRequest.status.in_(_PENDING_STATUSES),
                ReturnRequest.provider_refund_id != "",
            )
            .order_by(ReturnRequest.created_at.asc(), ReturnRequest.id.asc())
            .limit(max(1, min(limit, 200)))
            .all()
        )
    ]

    result = {
        "seen": len(candidate_ids),
        "succeeded": 0,
        "pending": 0,
        "canceled": 0,
        "review_required": 0,
        "provider_errors": 0,
        "skipped": 0,
    }

    for return_id in candidate_ids:
        snapshot = db.query(ReturnRequest).filter(ReturnRequest.id == return_id).first()
        if not snapshot or snapshot.status in _FINAL_STATUSES or not snapshot.provider_refund_id:
            result["skipped"] += 1
            continue

        refund_id = snapshot.provider_refund_id
        order_id = snapshot.order_id
        expected_amount = refund_money(snapshot.refund_amount, "stored refund amount")
        db.rollback()

        try:
            provider_refund = await fetch_yookassa_refund(refund_id)
            provider_status = str(provider_refund.get("status") or "").strip().lower()
            if not provider_status:
                raise HTTPException(status_code=409, detail="Provider refund has no status")
        except HTTPException:
            db.rollback()
            result["provider_errors"] += 1
            continue

        try:
            ret = (
                db.query(ReturnRequest)
                .filter(ReturnRequest.id == return_id)
                .with_for_update()
                .first()
            )
            order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
            if not ret or not order:
                db.rollback()
                result["skipped"] += 1
                continue
            if ret.status in _FINAL_STATUSES:
                db.rollback()
                result["skipped"] += 1
                continue
            if ret.provider_refund_id != refund_id:
                ret.status = "refund_review_required"
                order.payment_status = "refund_review_required"
                db.commit()
                result["review_required"] += 1
                continue

            actual_amount = provider_refund_amount(provider_refund, order.currency)
            if actual_amount != expected_amount or refund_money(ret.refund_amount, "stored refund amount") != expected_amount:
                ret.status = "refund_review_required"
                order.status = "refund_requested"
                order.payment_status = "refund_review_required"
                db.commit()
                result["review_required"] += 1
                continue

            apply_provider_refund_status(db, ret, order, provider_status)
            db.commit()
            if provider_status == "succeeded":
                result["succeeded"] += 1
            elif provider_status == "canceled":
                result["canceled"] += 1
            else:
                result["pending"] += 1
        except HTTPException:
            db.rollback()
            result["review_required"] += 1
        except Exception:
            db.rollback()
            result["provider_errors"] += 1

    return result
