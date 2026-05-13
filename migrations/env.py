"""Alembic environment.

Reads the database URL from $DATABASE_URL at runtime (so the same migrations work
locally and on Railway with no config changes), and only manages this app's tables
— the eval framework's `evaluations` table lives in the same Postgres but is
owned by eval/store.py and is excluded here.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project importable so we can grab the Base + models.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Import the Base AFTER load_dotenv so the models are registered against it.
from app.repositories.memory_repository import MemoryBase  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the DATABASE_URL at runtime
database_url = os.environ.get("DATABASE_URL", "")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = MemoryBase.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """Don't touch tables we don't own (e.g. the eval framework's `evaluations`).

    Without this, autogenerate would propose dropping any table it doesn't see in
    target_metadata — which would happily wipe the eval data.
    """
    if type_ == "table" and name not in target_metadata.tables:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
