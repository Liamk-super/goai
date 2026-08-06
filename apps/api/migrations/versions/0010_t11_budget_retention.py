"""Add durable T11 budget reservations, retention policy and deletion tombstones.

Forward: appends tenant-scoped operational facts and a maintenance-only deletion
escape hatch for records whose business bodies must be erased on request.
Rollback: drops only the new T11 tables and restores the strict append-only
function. Published earlier migrations are never edited.
"""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0010_t11_budget_retention"
down_revision = "0009_t10_persistent_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION launchscope_reject_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE owner_name text;
        BEGIN
            SELECT tableowner INTO owner_name FROM pg_tables
             WHERE schemaname = TG_TABLE_SCHEMA AND tablename = TG_TABLE_NAME;
            IF current_setting('app.retention_delete', true) = 'on' AND current_user = owner_name THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'append-only fact cannot be updated or deleted: %', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TABLE budget_reservation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            category varchar(64) NOT NULL,
            currency varchar(8) NOT NULL DEFAULT 'USD',
            limit_amount numeric(20,6) NOT NULL CHECK (limit_amount >= 0),
            reserved_amount numeric(20,6) NOT NULL CHECK (reserved_amount >= 0),
            consumed_amount numeric(20,6) NOT NULL DEFAULT 0 CHECK (consumed_amount >= 0),
            released_amount numeric(20,6) NOT NULL DEFAULT 0 CHECK (released_amount >= 0),
            status varchar(32) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            CHECK (consumed_amount + released_amount <= reserved_amount)
        );
        CREATE INDEX ix_budget_reservation_tenant_run ON budget_reservation (tenant_id, run_id, category);

        CREATE TABLE retention_policy (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            temporary_days integer NOT NULL DEFAULT 7 CHECK (temporary_days BETWEEN 1 AND 365),
            evidence_days integer NOT NULL DEFAULT 90 CHECK (evidence_days BETWEEN 1 AND 3650),
            trace_body_days integer NOT NULL DEFAULT 30 CHECK (trace_body_days BETWEEN 1 AND 365),
            metrics_days integer NOT NULL DEFAULT 365 CHECK (metrics_days BETWEEN 1 AND 3650),
            audit_days integer NOT NULL DEFAULT 365 CHECK (audit_days BETWEEN 1 AND 3650),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id)
        );

        CREATE TABLE deletion_tombstone (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            target_type varchar(32) NOT NULL,
            target_id uuid NOT NULL,
            target_sha256 varchar(64) NOT NULL CHECK (target_sha256 ~ '^[0-9a-f]{64}$'),
            actor_id varchar(255) NOT NULL,
            reason varchar(500) NOT NULL,
            result jsonb NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, target_type, target_id)
        );
        CREATE INDEX ix_deletion_tombstone_tenant_time ON deletion_tombstone (tenant_id, occurred_at);
        """
    )
    enable_tenant_rls(op, ("budget_reservation", "retention_policy", "deletion_tombstone"))
    grant_runtime(op, ("budget_reservation", "retention_policy"))
    op.execute("GRANT SELECT ON TABLE deletion_tombstone TO launchscope_runtime;")
    install_append_only_trigger(op, "deletion_tombstone")


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS deletion_tombstone CASCADE;
        DROP TABLE IF EXISTS retention_policy CASCADE;
        DROP TABLE IF EXISTS budget_reservation CASCADE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION launchscope_reject_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'append-only fact cannot be updated or deleted: %', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
