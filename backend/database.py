from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    Index,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
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


@event.listens_for(Mapper, "after_mapper_constructed")
def _upgrade_legacy_inventory_movement_schema(mapper: Mapper, _class) -> None:
    """Keep legacy ORM metadata aligned with Alembic revision 0041.

    ``models.py`` predates first-class physical-return movements. Production is
    migrated by Alembic, while many deterministic unit tests intentionally use
    ``Base.metadata.create_all`` against SQLite. The metadata adapter therefore
    mirrors the production contract exactly enough for both paths to enforce
    the same invariants instead of giving tests a weaker/different schema.
    """
    table = mapper.local_table
    if getattr(table, "name", "") != "inventory_movements":
        return

    kind_constraint = next(
        (
            item
            for item in list(table.constraints)
            if isinstance(item, CheckConstraint)
            and item.name == "ck_inventory_movements_kind"
        ),
        None,
    )
    if kind_constraint is not None and "'return'" not in str(kind_constraint.sqltext):
        table.constraints.remove(kind_constraint)
        table.append_constraint(
            CheckConstraint(
                "kind IN ('reserve', 'release', 'commit', 'return')",
                name="ck_inventory_movements_kind",
            )
        )

    legacy_unique = next(
        (
            item
            for item in list(table.constraints)
            if isinstance(item, UniqueConstraint)
            and item.name == "uq_inventory_movement_order_variant_kind"
        ),
        None,
    )
    if legacy_unique is not None:
        table.constraints.remove(legacy_unique)

    existing_index_names = {index.name for index in table.indexes}
    core_predicate = text("kind IN ('reserve','release','commit')")
    if "uq_inventory_movement_core_kind" not in existing_index_names:
        Index(
            "uq_inventory_movement_core_kind",
            table.c.order_id,
            table.c.variant_id,
            table.c.kind,
            unique=True,
            postgresql_where=core_predicate,
            sqlite_where=core_predicate,
        )
    return_predicate = text(
        "kind = 'return' AND source LIKE 'reverse_logistics_event:%'"
    )
    if "uq_inventory_movement_reverse_event_source" not in existing_index_names:
        Index(
            "uq_inventory_movement_reverse_event_source",
            table.c.source,
            unique=True,
            postgresql_where=return_predicate,
            sqlite_where=return_predicate,
        )


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
