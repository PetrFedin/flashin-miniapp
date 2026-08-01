import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException

from ..database import utcnow_naive


_PROMO_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
_ALLOWED_DISCOUNT_TYPES = frozenset({"percent", "fixed"})


@dataclass(frozen=True)
class PromoDefinition:
    code: str
    discount_type: str
    discount_value: float
    min_amount: float
    max_uses: int
    active: bool
    expires_at: datetime | None


def _finite_non_negative(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be numeric")
    if not math.isfinite(number) or number < 0:
        raise HTTPException(status_code=400, detail=f"{field} must be non-negative")
    return round(number, 2)


def _non_negative_integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{field} must be a non-negative integer")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{field} must be a non-negative integer")
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise HTTPException(status_code=400, detail=f"{field} must be a non-negative integer")
    return int(number)


def _normalize_expiry(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def normalize_promo_definition(
    *,
    code: object,
    discount_type: object,
    discount_value: object,
    min_amount: object,
    max_uses: object,
    active: object,
    expires_at: datetime | None,
) -> PromoDefinition:
    normalized_code = str(code or "").strip().upper()
    if not _PROMO_CODE_PATTERN.fullmatch(normalized_code):
        raise HTTPException(
            status_code=400,
            detail="Promo code must contain 3-64 uppercase letters, digits, underscores, or hyphens",
        )

    normalized_type = str(discount_type or "").strip().lower()
    if normalized_type not in _ALLOWED_DISCOUNT_TYPES:
        raise HTTPException(status_code=400, detail="Discount type must be percent or fixed")

    normalized_value = _finite_non_negative(discount_value, "Discount value")
    if normalized_type == "percent" and normalized_value > 100:
        raise HTTPException(status_code=400, detail="Percent discount cannot exceed 100")

    normalized_minimum = _finite_non_negative(min_amount, "Minimum amount")
    normalized_max_uses = _non_negative_integer(max_uses, "Maximum uses")
    normalized_active = bool(active)
    normalized_expiry = _normalize_expiry(expires_at)
    if normalized_active and normalized_expiry and normalized_expiry <= utcnow_naive():
        raise HTTPException(status_code=400, detail="Active promo code must expire in the future")

    return PromoDefinition(
        code=normalized_code,
        discount_type=normalized_type,
        discount_value=normalized_value,
        min_amount=normalized_minimum,
        max_uses=normalized_max_uses,
        active=normalized_active,
        expires_at=normalized_expiry,
    )
