import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend import catalog_demand_models  # noqa: F401
from backend import catalog_models  # noqa: F401
from backend import checkout_models  # noqa: F401
from backend import pilot_models  # noqa: F401
from backend import provider_models  # noqa: F401
from backend import (  # noqa: F401
    business_event_models,
    model_constraints,
    models,
    notification_models,
)
from backend.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Use the same runtime database URL as the application.

    The value in alembic.ini is only a local fallback. Production migrations
    must never silently connect with the development credentials embedded in
    that file.
    """
    url = (os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required for migrations")
    try:
        make_url(url)
    except ArgumentError as exc:
        raise RuntimeError("DATABASE_URL is invalid") from exc
    return url


def run_migrations_offline():
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
        pool_pre_ping=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
