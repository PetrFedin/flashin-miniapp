from sqlalchemy.orm import Session
from ..services.event_dispatcher import process_pending_events


def run_event_dispatcher(db: Session) -> int:
    return process_pending_events(db)
