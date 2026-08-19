import json
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import CustomerTimelineEvent


def _json_default(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("Decimal payload values must be finite")
        # Timeline payloads retain the existing JSON-number contract.
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def add_timeline_event(
    db: Session,
    customer_id: int,
    event_type: str,
    title: str,
    payload: dict | None = None,
) -> None:
    db.add(
        CustomerTimelineEvent(
            customer_id=customer_id,
            event_type=event_type,
            title=title,
            payload=json.dumps(
                payload or {},
                ensure_ascii=False,
                allow_nan=False,
                default=_json_default,
            ),
        )
    )
