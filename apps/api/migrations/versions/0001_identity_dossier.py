"""Create tenant, workspace, project, version and material metadata tables.

Forward: this is the expand migration for the identity/dossier boundary.
Rollback: drops only the objects introduced by this revision; later revisions
must be downgraded first.  No already-applied migration is edited in place.
"""

from __future__ import annotations

from alembic import op

from migrations._common import create_runtime_role, enable_tenant_rls, grant_runtime

revision = "0001_identity_dossier"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = ("workspace", "project", "product_version", "material", "product_profile")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    create_runtime_role(op)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION launchscope_current_tenant_id()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
            SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
        $$;

        CREATE OR REPLACE FUNCTION launchscope_set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$;

        CREATE OR REPLACE FUNCTION launchscope_reject_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'append-only fact cannot be updated or deleted: %', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE tenant (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug varchar(120) NOT NULL UNIQUE,
            status varchar(32) NOT NULL DEFAULT 'ACTIVE',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );

        CREATE TABLE workspace (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            name varchar(200) NOT NULL,
            status varchar(32) NOT NULL DEFAULT 'ACTIVE',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, name),
            FOREIGN KEY (tenant_id) REFERENCES tenant(id)
        );
        CREATE INDEX ix_workspace_tenant_created ON workspace (tenant_id, created_at);

        CREATE TABLE project (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name varchar(200) NOT NULL,
            dossier_status varchar(32) NOT NULL DEFAULT 'ACTIVE',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspace(tenant_id, id)
        );
        CREATE INDEX ix_project_tenant_updated ON project (tenant_id, updated_at);

        CREATE TABLE product_version (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            version_number integer NOT NULL CHECK (version_number > 0),
            label varchar(100) NOT NULL,
            stage varchar(64) NOT NULL DEFAULT 'DRAFT',
            source_version varchar(100),
            status varchar(32) NOT NULL DEFAULT 'DRAFT',
            submitted_by varchar(255),
            submitted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, project_id, version_number),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id)
        );
        CREATE INDEX ix_product_version_tenant_project ON product_version (tenant_id, project_id, version_number);

        CREATE TABLE material (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            source_type varchar(64) NOT NULL,
            object_key varchar(1024) NOT NULL
                CHECK (object_key !~ '(^/|(^|/)\\.\\.(/|$))'),
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-fA-F]{64}$'),
            size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
            mime_type varchar(255) NOT NULL,
            display_name varchar(255) NOT NULL,
            trust_level varchar(16) NOT NULL,
            ingest_status varchar(32) NOT NULL DEFAULT 'QUARANTINED',
            submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, object_key),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );
        CREATE INDEX ix_material_tenant_version ON material (tenant_id, product_version_id, created_at);

        CREATE TABLE product_profile (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            confirmed_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
            confirmation_status varchar(32) NOT NULL DEFAULT 'CONFIRMED',
            confirmed_by varchar(255) NOT NULL,
            confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            supersedes_id uuid,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_id) REFERENCES product_profile(tenant_id, id)
        );
        CREATE INDEX ix_product_profile_tenant_version ON product_profile (tenant_id, product_version_id, confirmed_at);
        """
    )
    enable_tenant_rls(op, _TABLES)
    grant_runtime(op, _TABLES)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS launchscope_reject_append_only();")
    op.execute("DROP FUNCTION IF EXISTS launchscope_set_updated_at();")
    op.execute("DROP FUNCTION IF EXISTS launchscope_current_tenant_id();")
