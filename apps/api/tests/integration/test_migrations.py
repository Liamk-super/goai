"""Migration ordering and repeatability checks."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text


def _config(database) -> Config:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.url.render_as_string(hide_password=False))
    return config


def test_migrations_reach_head_and_repeat_without_new_rows(database) -> None:
    config = _config(database)
    command.upgrade(config, "head")
    first = database.connect()
    try:
        version_before = first.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        first.close()
    command.upgrade(config, "head")
    second = database.connect()
    try:
        version_after = second.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        tables = {row[0] for row in second.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
    finally:
        second.close()
    assert version_before == version_after == "0014_v02_publisher_role"
    assert {
        "project",
        "product_version",
        "evaluation_run",
        "stage",
        "task",
        "evidence",
        "finding",
        "decision",
        "memory_candidate",
        "memory_item",
        "rag_retrieval",
        "agentteams_run_binding",
        "matrix_event_receipt",
    } <= tables
