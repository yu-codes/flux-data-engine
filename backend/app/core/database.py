"""SQLAlchemy engine, session factory and the declarative base.

Metadata is transactional platform state only: sources, datasets, schemas,
models, executions, results, applications. Bulk data lives in the object store
as Parquet, never in Postgres.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every module's ORM mapping."""


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
    echo=_settings.debug,
)

#  SQLite ignores foreign keys unless each connection asks for them, so a
#  constraint the deployment enforces is one the test suite never mentions.
#  A pipeline execution violating a leftover foreign key to `models` reached a
#  running PostgreSQL that way; the difference is not worth keeping.
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(connection, _record) -> None:  # noqa: ANN001
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed on success."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone session for startup tasks and workers."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


#  `app/modules`, located from this file rather than from the working
#  directory, because migrations run from wherever alembic was invoked.
_MODULES = Path(__file__).resolve().parent.parent / "modules"


def import_all_orm_models() -> None:
    """Import every ORM module so `Base.metadata` is complete.

    Discovered rather than listed. The list version fell behind the moment the
    modules were reorganised - `orchestration`, `evaluation`, `reporting`,
    `jobs`, workspaces and api keys were all missing from it - and the way that
    shows up is the worst kind: `alembic revision --autogenerate` sees tables
    in the database that no model describes, and helpfully writes
    `op.drop_table` for every one of them.

    Nothing in the running application depends on this, because each service
    imports its own ORM, so the gap stayed invisible until somebody generated a
    migration. A list that cannot fall behind is worth more here than an
    explicit one.
    """
    for path in sorted(_MODULES.glob("*/infrastructure/*orm*.py")):
        parts = path.relative_to(_MODULES.parent.parent).with_suffix("").parts
        importlib.import_module(".".join(parts))
