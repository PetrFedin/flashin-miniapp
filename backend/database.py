from datetime import UTC, datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapper, sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def utcnow_naive(_context=None) -> datetime:
    """Return current UTC while preserving the existing naive DB contract.

    Existing columns are PostgreSQL `timestamp without time zone`. Keeping one
    boundary helper avoids mixing local time with UTC without requiring an
    unsafe in-place type migration of every timestamp column.
    """
    return datetime.now(UTC).replace(tzinfo=None)


@event.listens_for(Mapper, "mapper_configured")
def _upgrade_legacy_utcnow_defaults(mapper: Mapper, _class) -> None:
    """Replace legacy SQLAlchemy `datetime.utcnow` defaults at mapper setup.

    `models.py` predates Python's deprecation of `datetime.utcnow`. This adapter
    keeps the schema and stored values unchanged while routing all legacy model
    defaults through the explicit UTC helper. New models must use
    `utcnow_naive` directly.
    """
    for column in mapper.columns:
        default = column.default
        if default is None or not default.is_callable:
            continue
        if getattr(default.arg, "__name__", "") != "utcnow":
            continue
        wrapped = getattr(default.arg, "__wrapped__", None)
        if wrapped is not None and getattr(wrapped, "__name__", "") != "utcnow":
            continue
        default.arg = utcnow_naive


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
