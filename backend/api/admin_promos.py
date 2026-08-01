from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PromoCode
from ..schemas import PromoCodeCreate
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.promo_definitions import normalize_promo_definition
from ..services.rbac import require_permission


router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/promocodes")
def admin_create_promo(
    payload: PromoCodeCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "promo.write")
    definition = normalize_promo_definition(
        code=payload.code,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        min_amount=payload.min_amount,
        max_uses=payload.max_uses,
        active=payload.active,
        expires_at=payload.expires_at,
    )

    try:
        promo = PromoCode(
            code=definition.code,
            discount_type=definition.discount_type,
            discount_value=definition.discount_value,
            min_amount=definition.min_amount,
            max_uses=definition.max_uses,
            active=definition.active,
            expires_at=definition.expires_at,
        )
        db.add(promo)
        db.flush()
        log_admin_action(
            db,
            admin,
            "promocode.create",
            "promocode",
            promo.id,
            {
                "code": definition.code,
                "discount_type": definition.discount_type,
                "discount_value": definition.discount_value,
            },
        )
        db.commit()
        return {"ok": True, "id": promo.id}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Promo code already exists or is invalid") from exc
    except Exception:
        db.rollback()
        raise
