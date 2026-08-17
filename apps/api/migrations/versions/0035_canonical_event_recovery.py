"""Append-only recovery for a canonical v4 event hidden by a synthetic failure."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0035_canonical_event_recovery"
down_revision = "0034_run_limit_amendment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_canonical_event_recovery (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            control_epoch integer NOT NULL CHECK (control_epoch >= 0),
            matrix_event_id varchar(255) NOT NULL,
            source_payload_sha256 varchar(64) NOT NULL CHECK (source_payload_sha256 ~ '^[0-9a-f]{64}$'),
            reason varchar(1000) NOT NULL,
            authorized_by varchar(255) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            correlation_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, task_id, dispatch_epoch, matrix_event_id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );

        CREATE TABLE run_canonical_event_replay (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            recovery_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            matrix_event_id varchar(255) NOT NULL,
            canonical_payload_sha256 varchar(64) NOT NULL CHECK (canonical_payload_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, recovery_id),
            FOREIGN KEY (tenant_id, recovery_id) REFERENCES run_canonical_event_recovery(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        """
    )
    tables = ("run_canonical_event_recovery", "run_canonical_event_replay")
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)
    for table in tables:
        install_append_only_trigger(op, table)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS run_canonical_event_replay CASCADE;
        DROP TABLE IF EXISTS run_canonical_event_recovery CASCADE;
        """
    )
