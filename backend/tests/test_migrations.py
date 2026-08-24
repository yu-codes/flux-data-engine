"""The migrations and the models have to agree, and both have to run.

Fifteen migrations had never been executed by the suite. The tests build their
schema with `Base.metadata.create_all`, so a column added to an ORM class and
forgotten in a migration passes every test and fails on the first real
deployment - and a migration that alters existing rows, like the one that
demoted pipeline intermediates, had nothing checking it at all.

Two things are checked here, on a throwaway database:

* `upgrade head` produces the schema the ORM expects - same tables, same
  columns. This is the drift check, and it is the one that would have caught a
  forgotten migration.
* every migration can be undone. A downgrade nobody has ever run is not a
  rollback plan, it is a paragraph in a runbook.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[1]

#  Tables the ORM does not describe: alembic's own bookkeeping.
NOT_OURS = {"alembic_version"}


def _alembic_config(url: str):
    from alembic.config import Config

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migrated(tmp_path):
    """A database built the way a deployment builds one: by migrating."""
    from alembic import command

    url = f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}"
    command.upgrade(_alembic_config(url), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def _model_schema() -> dict[str, set[str]]:
    from app.core.database import Base, import_all_orm_models

    import_all_orm_models()
    return {
        name: {column.name for column in table.columns}
        for name, table in Base.metadata.tables.items()
    }


def test_migrating_produces_the_schema_the_models_expect(migrated):
    """The check that catches a column added to an ORM class and nowhere else."""
    inspector = inspect(migrated)
    migrated_tables = set(inspector.get_table_names()) - NOT_OURS
    expected = _model_schema()

    missing = sorted(set(expected) - migrated_tables)
    assert not missing, f"no migration creates: {missing}"

    extra = sorted(migrated_tables - set(expected))
    assert not extra, f"migrations create tables no model describes: {extra}"


def test_every_column_a_model_declares_exists_after_migrating(migrated):
    inspector = inspect(migrated)
    problems: list[str] = []
    for table, columns in _model_schema().items():
        if table not in set(inspector.get_table_names()):
            continue
        actual = {c["name"] for c in inspector.get_columns(table)}
        for column in sorted(columns - actual):
            problems.append(f"{table}.{column} is in the model and not in the schema")
        for column in sorted(actual - columns):
            problems.append(f"{table}.{column} is in the schema and not in the model")
    assert not problems, "\n".join(problems)


def test_nothing_that_points_at_a_runnable_carries_a_foreign_key(migrated):
    """A polymorphic id cannot reference one table, and must not claim to.

    `executions.target_id` kept the foreign key `model_id` had when a model
    was the only thing that could be executed, and PostgreSQL rejected the
    first pipeline execution because of it. SQLite said nothing, because it
    does not enforce foreign keys unless asked - so this asks the schema
    instead of the database.
    """
    inspector = inspect(migrated)
    offenders = []
    for table, column in (("executions", "target_id"), ("schedules", "target_id")):
        if table not in set(inspector.get_table_names()):
            continue
        for key in inspector.get_foreign_keys(table):
            if column in key["constrained_columns"]:
                offenders.append(f"{table}.{column} -> {key['referred_table']}")
    assert not offenders, (
        "a target can be any kind of runnable, so it cannot reference one "
        f"table: {offenders}"
    )


def test_the_schedule_target_rename_kept_the_data(tmp_path):
    """A rename is the migration most likely to lose something.

    Rows are written at the revision before the rename and read back after it,
    because "the column exists" and "what was in it is still there" are
    different statements.
    """
    from sqlalchemy import text

    from alembic import command

    url = f"sqlite+pysqlite:///{tmp_path / 'rename.db'}"
    config = _alembic_config(url)
    command.upgrade(config, "b8d1a4f92e57")

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO schedules (id, name, model_id, kind, status, "
                "input_payload, parameters, run_count, failure_count, "
                "created_at, updated_at, description) "
                "VALUES ('sch_1', 'nightly', 'mdl_7', 'prediction', 'active', "
                "'{}', '{}', 0, 0, '2026-01-01', '2026-01-01', '')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT target_id, target_type FROM schedules WHERE id = 'sch_1'")
        ).one()
    engine.dispose()

    assert row.target_id == "mdl_7"
    #  Everything that existed before the rename was a model schedule.
    assert row.target_type == "model"


def test_every_migration_can_be_undone(tmp_path):
    """A downgrade path nobody has run is not a rollback plan."""
    from alembic import command

    url = f"sqlite+pysqlite:///{tmp_path / 'roundtrip.db'}"
    config = _alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(url)
    remaining = set(inspect(engine).get_table_names()) - NOT_OURS
    engine.dispose()
    assert not remaining, f"downgrade left tables behind: {sorted(remaining)}"

    #  And the way back up still works on a database that has been down.
    command.upgrade(config, "head")
