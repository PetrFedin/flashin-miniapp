from sqlalchemy.orm import Session


SUPPORTED_DELIVERY_TYPES = frozenset({"pickup", "courier"})


def calculate_delivery_price(
    _db: Session,
    _delivery_type: str,
    _address: str = "",
) -> float:
    """Retired legacy tariff source.

    Delivery pricing is quote-authoritative. Callers must resolve a normalized
    address to an authoritative DeliveryQuote and carry that immutable quote
    through checkout, Order and Shipment. Keeping this symbol fail-closed makes
    stale imports obvious without preserving a second pricing authority.
    """
    raise RuntimeError(
        "Delivery price is quote-authoritative; request a DeliveryQuote"
    )
