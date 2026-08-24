"""Alembic environment.

The database URL comes from the application settings, so migrations and the
running service can never disagree about where the metadata lives.

A URL set on the config wins, though. Without that exception, every alembic
invocation silently retargets whatever `FLUX_DATABASE_URL` happens to point
at - which makes "migrate this throwaway database" impossible to ask for, and
makes it far too easy to migrate the wrong one by accident.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.database import Base, import_all_orm_models

config = context.config
if config.config_file_name is not None:
    #  `disable_existing_loggers` defaults to True, which would silence every
    #  logger the application had already configured. That is harmless when
    #  alembic runs as its own process and not at all harmless in-process,
    #  where it turns the platform quiet for the rest of the run.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

#  Import every module's ORM mapping so autogenerate sees the whole schema.
import_all_orm_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
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
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
