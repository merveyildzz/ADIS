"""Smoke test: the full Alembic migration chain applies and rolls back
cleanly against a scratch SQLite database — including the three rule-related
migrations (custom_rules, applied_rules, custom_rules.upload_id) — the last
of which had to batch-alter an existing SQLite table to add a FK, the same
pattern as 5e88d2c39f94's audit_log.record_id addition.
"""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def alembic_config(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from app.config import get_settings
    get_settings.cache_clear()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "db"))
    yield config, db_path

    get_settings.cache_clear()


def test_upgrade_head_creates_custom_rules_with_upload_id_and_applied_rules(alembic_config):
    config, db_path = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    assert "custom_rules" in tables
    assert "applied_rules" in tables
    columns = {c["name"] for c in inspector.get_columns("custom_rules")}
    assert "upload_id" in columns
    engine.dispose()


def test_downgrade_one_step_removes_custom_rules_upload_id_column_only(alembic_config):
    config, db_path = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "-1")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("custom_rules")}
    assert "upload_id" not in columns
    # applied_rules/custom_rules themselves predate this migration
    assert "applied_rules" in inspector.get_table_names()
    assert "custom_rules" in inspector.get_table_names()
    engine.dispose()


def test_downgrade_two_steps_removes_applied_rules_table_cleanly(alembic_config):
    config, db_path = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "-2")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = inspect(engine).get_table_names()
    assert "applied_rules" not in tables
    assert "custom_rules" in tables
    engine.dispose()


def test_downgrade_three_steps_removes_custom_rules_table_cleanly(alembic_config):
    config, db_path = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "-3")

    engine = create_engine(f"sqlite:///{db_path}")
    tables = inspect(engine).get_table_names()
    assert "custom_rules" not in tables
    assert "applied_rules" not in tables
    # every other table from before these migrations is untouched
    assert "raw_uploads" in tables
    assert "cleaned_records" in tables
    engine.dispose()
