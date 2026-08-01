import re
from dataclasses import dataclass

from fastapi import HTTPException


_ALLOWED_DELIVERY_TYPES = frozenset({"pickup", "courier"})
_PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9()\-\s]{5,24}$")


@dataclass(frozen=True)
class NormalizedCheckoutInput:
    name: str
    phone: str
    delivery_type: str
    address: str
    comment: str


def _collapse_spaces(value: object) -> str:
    return " ".join(str(value or "").split())


def _trim_multiline(value: object) -> str:
    return str(value or "").strip()


def normalize_checkout_input(
    *,
    name: object,
    phone: object,
    delivery_type: object,
    address: object,
    comment: object,
) -> NormalizedCheckoutInput:
    normalized_name = _collapse_spaces(name)
    if len(normalized_name) < 2:
        raise HTTPException(status_code=400, detail="Name must contain at least two characters")
    if len(normalized_name) > 120:
        raise HTTPException(status_code=400, detail="Name is too long")

    normalized_phone = _collapse_spaces(phone)
    phone_digits = re.sub(r"\D", "", normalized_phone)
    if not _PHONE_PATTERN.fullmatch(normalized_phone) or not 7 <= len(phone_digits) <= 15:
        raise HTTPException(status_code=400, detail="Phone number is invalid")

    normalized_delivery_type = _collapse_spaces(delivery_type).lower()
    if normalized_delivery_type not in _ALLOWED_DELIVERY_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported delivery type")

    normalized_address = _collapse_spaces(address)
    if normalized_delivery_type == "courier":
        if len(normalized_address) < 8:
            raise HTTPException(status_code=400, detail="Courier address is too short")
        if len(normalized_address) > 500:
            raise HTTPException(status_code=400, detail="Address is too long")
    else:
        normalized_address = ""

    normalized_comment = _trim_multiline(comment)
    if len(normalized_comment) > 1000:
        raise HTTPException(status_code=400, detail="Comment is too long")

    return NormalizedCheckoutInput(
        name=normalized_name,
        phone=normalized_phone,
        delivery_type=normalized_delivery_type,
        address=normalized_address,
        comment=normalized_comment,
    )
