from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..delivery_models import DeliveryQuote
from ..delivery_schemas import DeliveryQuoteCreate, DeliveryQuoteOut
from ..models import Customer
from ..security import get_current_customer
from ..services.delivery_authority import DeliveryAuthorityError, create_delivery_quote

router = APIRouter(prefix="/delivery-quotes", tags=["delivery-quotes"])


def _http_delivery_error(exc: DeliveryAuthorityError) -> HTTPException:
    if exc.code in {"unsupported_address", "address_incomplete", "unsupported_delivery_type"}:
        status = 422
    elif exc.code == "provider_unavailable":
        status = 503
    else:
        status = 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.post("", response_model=DeliveryQuoteOut)
def create_quote(
    payload: DeliveryQuoteCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    address = payload.address
    try:
        quote = create_delivery_quote(
            db,
            customer_id=customer.id,
            delivery_type=payload.delivery_type,
            country_code=address.country_code if address else "RU",
            region=address.region if address else "",
            city=address.city if address else "",
            postal_code=address.postal_code if address else "",
            address_line=address.address_line if address else "",
        )
        db.commit()
        db.refresh(quote)
        return quote
    except DeliveryAuthorityError as exc:
        db.rollback()
        raise _http_delivery_error(exc) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/{quote_public_id}", response_model=DeliveryQuoteOut)
def get_quote(
    quote_public_id: str,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    quote = (
        db.query(DeliveryQuote)
        .filter(
            DeliveryQuote.public_id == str(quote_public_id or "").strip(),
            DeliveryQuote.customer_id == customer.id,
        )
        .first()
    )
    if quote is None:
        raise HTTPException(status_code=404, detail="Delivery quote not found")
    return quote
