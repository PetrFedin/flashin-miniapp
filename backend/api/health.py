from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db

router = APIRouter(tags=["health"])
BACKEND_ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def _expected_migration_heads() -> frozenset[str]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    heads = frozenset(scripts.get_heads())
    if not heads:
        raise RuntimeError("Alembic has no migration head")
    return heads


@router.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        current_heads = frozenset(
            str(value)
            for value in db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if value
        )
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
