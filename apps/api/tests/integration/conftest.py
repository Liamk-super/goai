"""Opt-in PostgreSQL fixtures for T4 integration evidence."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from launchscope_api.infrastructure.db.session import create_database_engine
from launchscope_domain.value_objects import TenantScope


@pytest.fixture(scope="session")
def database() -> Engine:
    url = os.getenv("LAUNCHSCOPE_TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url or not url.startswith(("postgresql://", "postgresql+")):
        pytest.skip("set LAUNCHSCOPE_TEST_DATABASE_URL to run PostgreSQL integration tests")
    engine = create_database_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL is not available: {exc}")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def runtime_engine(database: Engine) -> Engine:
    engine = create_database_engine(
        database.url.render_as_string(hide_password=False),
        application_role="launchscope_runtime",
    )
    yield engine
    engine.dispose()


def seed_tenant(database: Engine) -> dict[str, UUID | TenantScope]:
    tenant_id = uuid4()
    workspace_id = uuid4()
    project_id = uuid4()
    version_id = uuid4()
    run_id = uuid4()
    now = datetime.now(UTC)
    with database.begin() as connection:
        connection.execute(
            text("INSERT INTO tenant (id, slug) VALUES (:id, :slug)"),
            {"id": tenant_id, "slug": f"test-{tenant_id}"},
        )
        connection.execute(
            text("INSERT INTO workspace (id, tenant_id, name) VALUES (:id, :tenant_id, :name)"),
            {"id": workspace_id, "tenant_id": tenant_id, "name": f"workspace-{tenant_id}"},
        )
        connection.execute(
            text(
                "INSERT INTO project (id, tenant_id, workspace_id, name) "
                "VALUES (:id, :tenant_id, :workspace_id, :name)"
            ),
            {"id": project_id, "tenant_id": tenant_id, "workspace_id": workspace_id, "name": "T4 test project"},
        )
        connection.execute(
            text(
                "INSERT INTO product_version (id, tenant_id, project_id, version_number, label) "
                "VALUES (:id, :tenant_id, :project_id, 1, 'v1')"
            ),
            {"id": version_id, "tenant_id": tenant_id, "project_id": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO evaluation_run "
                "(id, tenant_id, project_id, product_version_id, status, standard_version, "
                "correlation_id, idempotency_key) "
                "VALUES (:id, :tenant_id, :project_id, :version_id, 'DRAFT', '1.0', :correlation_id, :idempotency_key)"
            ),
            {
                "id": run_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "version_id": version_id,
                "correlation_id": uuid4(),
                "idempotency_key": f"seed-run-{run_id}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO evidence "
                "(id, tenant_id, run_id, source_type, object_key, sha256, mime_type, evidence_level, trust_level) "
                "VALUES (:id, :tenant_id, :run_id, 'MATERIAL', :object_key, :sha256, 'text/plain', 'E1', 'E1')"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "run_id": run_id,
                "object_key": f"{tenant_id}/{project_id}/{version_id}/{run_id}/evidence.txt",
                "sha256": "0" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO memory_item (id, tenant_id, project_id, item_type, content) "
                "VALUES (:id, :tenant_id, :project_id, 'FACT', '{\"label\": \"tenant-local\"}'::jsonb)"
            ),
            {"id": uuid4(), "tenant_id": tenant_id, "project_id": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO trace_metadata "
                "(id, tenant_id, run_id, correlation_id, span_id, attributes) "
                "VALUES (:id, :tenant_id, :run_id, :correlation_id, :span_id, '{\"kind\": \"test\"}'::jsonb)"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "run_id": run_id,
                "correlation_id": uuid4(),
                "span_id": f"span-{run_id}",
            },
        )
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "project_id": project_id,
        "version_id": version_id,
        "run_id": run_id,
        "scope": TenantScope(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            product_version_id=version_id,
            run_id=run_id,
        ),
        "created_at": now,
    }


@pytest.fixture
def tenant_records(database: Engine) -> dict[str, UUID | TenantScope]:
    return seed_tenant(database)
