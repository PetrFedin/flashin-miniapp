from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from ..database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}
