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


def _append_partial_unique_index(
    table,
    *,
    name: str,
    columns: tuple[str, ...],
    predicate: str,
) -> None:
    """Mirror a production partial unique index in create_all test metadata."""

    if name in {index.name for index in table.indexes}:
        return
    where = text(predicate)
    Index(
        name,
        *(table.c[column] for column in columns),
        unique=True,
        postgresql_where=where,
        sqlite_where=where,
    )


@event.listens_for(Mapper, "after_mapper_constructed")
def _upgrade_legacy_authority_schema(mapper: Mapper, _class) -> None:
    """Keep legacy ORM metadata aligned with Alembic authority revisions.

    Production is migrated by Alembic, while deterministic unit tests often use
    ``Base.metadata.create_all`` against SQLite. The metadata adapter therefore
    mirrors the 0041 inventory-movement contract and the 0042 single-open
    MoySklad evidence invariants so tests never run against a weaker schema.
    """

    table = mapper.local_table
    table_name = getattr(table, "name", "")

    if table_name == "moysklad_conflicts":
        _append_partial_unique_index(
            table,
            name="uq_moysklad_conflict_open_stale_physical_return",
            columns=("moysklad_id", "conflict_type"),
            predicate=(
                "status = 'open' AND "
                "conflict_type = 'stale_stock_pending_physical_return'"
            ),
        )
        return

    if table_name == "stock_reconciliation_logs":
        _append_partial_unique_index(
            table,
            name="uq_stock_reconciliation_open_blocked_physical_return",
            columns=("variant_id",),
            predicate="status = 'open' AND action = 'blocked_physical_return'",
        )
        return

    if table_name != "inventory_movements":
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

    _append_partial_unique_index(
        table,
        name="uq_inventory_movement_core_kind",
        columns=("order_id", "variant_id", "kind"),
        predicate="kind IN ('reserve','release','commit')",
    )
    _append_partial_unique_index(
        table,
        name="uq_inventory_movement_reverse_event_source",
        columns=("source",),
        predicate="kind = 'return' AND source LIKE 'reverse_logistics_event:%'",
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
