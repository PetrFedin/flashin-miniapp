from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.database_readiness import (
    current_migration_heads,
    expected_migration_heads,
)

router = APIRouter(tags=["health"])


@lru_cache
def _expected_migration_heads() -> frozenset[str]:
    # Compatibility wrapper kept local so readiness tests and callers have one
    # stable seam while diagnostics reuse the same underlying migration graph.
    return expected_migration_heads()


@router.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        current_heads = current_migration_heads(db)
        expected_heads = _expected_migration_heads()
        if current_heads != expected_heads:
            raise HTTPException(
                status_code=503,
                detail="Database migrations are not current",
            )
    except HTTPException:
        db.rollback()
        raise
    except (SQLAlchemyError, RuntimeError):
        db.rollback()
        raise HTTPException(status_code=503, detail="Service is not ready")

    return {
        "status": "ready",
        "database": "ok",
        "migrations": "current",
    }
