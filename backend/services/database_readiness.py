from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def expected_migration_heads() -> frozenset[str]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    heads = frozenset(scripts.get_heads())
    if not heads:
        raise RuntimeError("Alembic has no migration head")
    return heads


def current_migration_heads(db: Session) -> frozenset[str]:
    return frozenset(
        str(value)
        for value in db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        if value
    )


def migrations_are_current(db: Session) -> bool:
    return current_migration_heads(db) == expected_migration_heads()


__all__ = [
    "current_migration_heads",
    "expected_migration_heads",
    "migrations_are_current",
]
