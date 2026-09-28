from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..catalog_models import ProductFeedback
from ..database import get_db
from ..models import Product
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/catalog/admin", tags=["catalog-admin-operations"])


@router.get("/feedback")
def admin_feedback_queue(
    status: Literal["published", "hidden"] | None = None,
    product_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Return a moderation queue without exposing customer contact/Telegram data."""

    require_permission(db, admin, "products.read")
    query = (
        db.query(ProductFeedback, Product)
        .join(Product, Product.id == ProductFeedback.product_id)
    )
    if status:
        query = query.filter(ProductFeedback.status == status)
    if product_id is not None:
        query = query.filter(ProductFeedback.product_id == product_id)
    rows = (
        query.order_by(ProductFeedback.updated_at.desc(), ProductFeedback.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": feedback.id,
            "product_id": feedback.product_id,
            "product_title": product.title,
            "rating": feedback.rating,
            "comment": feedback.comment,
            "status": feedback.status,
            "created_at": feedback.created_at,
            "updated_at": feedback.updated_at,
        }
        for feedback, product in rows
    ]
