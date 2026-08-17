from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .conftest import seed_tenant

REPORT_V22_TABLES = {
    "evidence_source_locator",
    "report_claim_citation",
    "public_demo_disclosure_acceptance",
    "public_demo_share",
    "report_export_artifact",
}


def test_report_v22_columns_tables_rls_and_unique_keys_exist(database) -> None:
    with database.connect() as connection:
        columns = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'evaluation_run'"
                )
            )
        }
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        }
        rls_tables = {
            row[0]
            for row in connection.execute(
                text("SELECT relname FROM pg_class WHERE relrowsecurity = true")
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                    "AND indexname LIKE 'uq_report_v22_%'"
                )
            )
        }
    assert {"input_snapshot_sha256", "content_fingerprint_sha256", "report_profile_ref"} <= columns
    assert tables >= REPORT_V22_TABLES
    assert rls_tables >= REPORT_V22_TABLES
    assert indexes == {
        "uq_report_v22_disclosure_policy",
        "uq_report_v22_export_cache",
        "uq_report_v22_export_idempotency",
        "uq_report_v22_public_token",
    }


def test_report_v22_tables_use_tenant_composite_foreign_keys(database) -> None:
    with database.connect() as connection:
        definitions = {
            (row[0], row[1])
            for row in connection.execute(
                text(
                    "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE contype = 'f' "
                    "AND conrelid::regclass::text = ANY(:tables)"
                ),
                {"tables": sorted(REPORT_V22_TABLES)},
            )
        }
    assert definitions
    for table, definition in definitions:
        assert "FOREIGN KEY (tenant_id," in definition, (table, definition)
        assert "REFERENCES" in definition


def test_full_evaluation_may_bind_baseline_but_recheck_still_requires_one(database) -> None:
    seeded = seed_tenant(database)
    candidate_id = uuid4()
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_run "
                "(id, tenant_id, project_id, product_version_id, status, standard_version, correlation_id, "
                "idempotency_key, run_kind) VALUES "
                "(:id, :tenant_id, :project_id, :version_id, 'DRAFT', '1.0', :correlation_id, :key, 'FULL_EVALUATION')"
            ),
            {
                "id": candidate_id,
                "tenant_id": seeded["tenant_id"],
                "project_id": seeded["project_id"],
                "version_id": seeded["version_id"],
                "correlation_id": uuid4(),
                "key": f"report-v22-{candidate_id}",
            },
        )
        connection.execute(
            text("UPDATE evaluation_run SET baseline_run_id = :baseline WHERE id = :candidate"),
            {"baseline": seeded["run_id"], "candidate": candidate_id},
        )
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO evaluation_run "
                "(id, tenant_id, project_id, product_version_id, status, standard_version, correlation_id, "
                "idempotency_key, run_kind) VALUES "
                "(:id, :tenant_id, :project_id, :version_id, 'DRAFT', '1.0', :correlation_id, :key, "
                "'USER_EVIDENCE_RECHECK')"
            ),
            {
                "id": uuid4(),
                "tenant_id": seeded["tenant_id"],
                "project_id": seeded["project_id"],
                "version_id": seeded["version_id"],
                "correlation_id": uuid4(),
                "key": f"recheck-without-baseline-{uuid4()}",
            },
        )


def test_cross_tenant_baseline_is_rejected(database) -> None:
    first = seed_tenant(database)
    second = seed_tenant(database)
    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(
            text(
                "UPDATE evaluation_run SET baseline_run_id = :foreign_run "
                "WHERE tenant_id = :tenant_id AND id = :run_id"
            ),
            {
                "foreign_run": first["run_id"],
                "tenant_id": second["tenant_id"],
                "run_id": second["run_id"],
            },
        )


def test_existing_material_routing_volume_upgrades_without_inventing_hashes(database) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.url.render_as_string(hide_password=False))
    with database.begin() as connection:
        connection.execute(
            text("UPDATE evaluation_run SET baseline_run_id = NULL WHERE run_kind = 'FULL_EVALUATION'")
        )
    command.downgrade(config, "0030_material_routing_v2")
    seeded = seed_tenant(database)
    command.upgrade(config, "head")
    with database.connect() as connection:
        row = connection.execute(
            text(
                "SELECT input_snapshot_sha256, content_fingerprint_sha256, report_profile_ref, baseline_run_id "
                "FROM evaluation_run WHERE tenant_id = :tenant_id AND id = :run_id"
            ),
            {"tenant_id": seeded["tenant_id"], "run_id": seeded["run_id"]},
        ).one()
    assert tuple(row) == (None, None, None, None)
