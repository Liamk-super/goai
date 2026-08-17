"""ADR 0018: durable material analysis, selection, Task scope, and read receipts."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0030_material_routing_v2"
down_revision = "0029_model_invoc_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        CREATE TABLE material_analysis (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            material_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            status varchar(32) NOT NULL CHECK (
                status IN ('QUEUED','PARSING','NEEDS_CONSENT','READY','PARTIAL','FAILED','EXCLUDED')
            ),
            attempt integer NOT NULL DEFAULT 0 CHECK (attempt BETWEEN 0 AND 2),
            parser_version varchar(80) NOT NULL,
            model_id varchar(200),
            manifest_object_key varchar(1024),
            manifest_sha256 varchar(64) CHECK (manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'),
            page_count integer NOT NULL DEFAULT 0 CHECK (page_count BETWEEN 0 AND 500),
            unit_count integer NOT NULL DEFAULT 0 CHECK (unit_count BETWEEN 0 AND 10000),
            coverage jsonb NOT NULL DEFAULT
                '{"total":0,"parsed":0,"visual_inspected":0,"uncovered_locators":[]}'::jsonb,
            error_code varchar(120),
            error_message varchar(2000),
            external_consent boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            completed_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, material_id, attempt),
            FOREIGN KEY (tenant_id, material_id) REFERENCES material(tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );
        CREATE INDEX ix_material_analysis_version_status
            ON material_analysis (tenant_id, product_version_id, status, updated_at);

        CREATE TABLE material_unit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            analysis_id uuid NOT NULL,
            material_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            parent_unit_id uuid,
            ordinal integer NOT NULL CHECK (ordinal BETWEEN 1 AND 10000),
            unit_type varchar(32) NOT NULL CHECK (
                unit_type IN ('DOCUMENT','SECTION','PAGE','PARAGRAPH','TABLE','IMAGE')
            ),
            locator jsonb NOT NULL,
            tags jsonb NOT NULL DEFAULT '[]'::jsonb,
            confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            contains_sensitive_data boolean NOT NULL DEFAULT false,
            object_key varchar(1024) NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            summary varchar(2000) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, analysis_id, ordinal),
            FOREIGN KEY (tenant_id, analysis_id) REFERENCES material_analysis(tenant_id, id),
            FOREIGN KEY (tenant_id, material_id) REFERENCES material(tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, parent_unit_id) REFERENCES material_unit(tenant_id, id)
        );
        CREATE INDEX ix_material_unit_analysis ON material_unit (tenant_id, analysis_id, ordinal);
        CREATE INDEX ix_material_unit_material ON material_unit (tenant_id, material_id, unit_type);

        CREATE TABLE material_selection (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            revision integer NOT NULL CHECK (revision > 0),
            idempotency_key varchar(255) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (length(request_sha256) = 64),
            object_key varchar(1024) NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            confirmed_by varchar(255) NOT NULL,
            confirmed_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, product_version_id, revision),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );

        CREATE TABLE material_selection_item (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            selection_id uuid NOT NULL,
            material_id uuid NOT NULL,
            analysis_id uuid NOT NULL,
            decision varchar(32) NOT NULL CHECK (decision IN ('INCLUDE','INCLUDE_PARTIAL','EXCLUDE')),
            acknowledged_uncovered_locators jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, selection_id, material_id),
            FOREIGN KEY (tenant_id, selection_id) REFERENCES material_selection(tenant_id, id),
            FOREIGN KEY (tenant_id, material_id) REFERENCES material(tenant_id, id),
            FOREIGN KEY (tenant_id, analysis_id) REFERENCES material_analysis(tenant_id, id)
        );

        CREATE TABLE task_material_scope (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            plan_id uuid NOT NULL,
            material_id uuid NOT NULL,
            analysis_id uuid NOT NULL,
            unit_ids jsonb NOT NULL,
            unit_refs jsonb NOT NULL,
            reason varchar(1000) NOT NULL,
            required boolean NOT NULL,
            scope_sha256 varchar(64) NOT NULL CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, plan_id) REFERENCES agent_plan(tenant_id, id),
            FOREIGN KEY (tenant_id, material_id) REFERENCES material(tenant_id, id),
            FOREIGN KEY (tenant_id, analysis_id) REFERENCES material_analysis(tenant_id, id)
        );
        CREATE INDEX ix_task_material_scope_task ON task_material_scope (tenant_id, task_id);

        CREATE TABLE material_read_receipt (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            agent_code varchar(120) NOT NULL,
            purpose varchar(500) NOT NULL,
            unit_refs jsonb NOT NULL,
            parameters_sha256 varchar(64) NOT NULL CHECK (parameters_sha256 ~ '^[0-9a-f]{64}$'),
            result_sha256 varchar(64) CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
            status varchar(32) NOT NULL CHECK (status IN ('STARTED','SUCCEEDED','SCOPE_DENIED','INTEGRITY_FAILED')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        CREATE INDEX ix_material_read_receipt_task
            ON material_read_receipt (tenant_id, task_id, created_at DESC);
        """
    )
    tables = (
        "material_analysis",
        "material_unit",
        "material_selection",
        "material_selection_item",
        "task_material_scope",
        "material_read_receipt",
    )
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)
    for table in (
        "material_unit",
        "material_selection",
        "material_selection_item",
        "task_material_scope",
        "material_read_receipt",
    ):
        install_append_only_trigger(op, table)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        """
        DROP TABLE IF EXISTS material_read_receipt CASCADE;
        DROP TABLE IF EXISTS task_material_scope CASCADE;
        DROP TABLE IF EXISTS material_selection_item CASCADE;
        DROP TABLE IF EXISTS material_selection CASCADE;
        DROP TABLE IF EXISTS material_unit CASCADE;
        DROP TABLE IF EXISTS material_analysis CASCADE;
        """
    )
