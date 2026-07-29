import os
import sys
from logging.config import fileConfig
from pathlib import Path

import sqlalchemy as sa
from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from backend import model_constraints, models, notification_models  # noqa: E402,F401
from backend.database import Base  # noqa: E402

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


def _ensure_version_column_capacity(connection) -> None:
    """Keep Alembic's version table compatible with descriptive revision IDs."""
    version_table = sa.Table(
        "alembic_version",
        sa.MetaData(),
        sa.Column("version_num", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("version_num", name="alembic_version_pkc"),
    )
    version_table.create(connection, checkfirst=True)

    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(128)"
            )
        )
    connection.commit()


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
        _ensure_version_column_capacity(connection)
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
