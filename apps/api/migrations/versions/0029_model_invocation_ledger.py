"""ADR 0017: delivery-scoped model admission and single-posting usage."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime

revision = "0029_model_invoc_ledger"
down_revision = "0028_run_conversation_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agentteams_task_delivery
            ADD COLUMN worker_name varchar(160),
            ADD COLUMN max_model_calls integer,
            ADD COLUMN accounting_mode varchar(32) NOT NULL DEFAULT 'COPAW_TASK_DELTA';
        UPDATE agentteams_task_delivery
        SET worker_name = agent_code, max_model_calls = 0
        WHERE worker_name IS NULL OR max_model_calls IS NULL;
        ALTER TABLE agentteams_task_delivery
            ALTER COLUMN worker_name SET NOT NULL,
            ALTER COLUMN max_model_calls SET NOT NULL,
            ADD CONSTRAINT agentteams_task_delivery_max_model_calls_check
                CHECK (max_model_calls >= 0),
            ADD CONSTRAINT agentteams_task_delivery_accounting_mode_check
                CHECK (accounting_mode IN ('COPAW_TASK_DELTA', 'GATEWAY_DELIVERY'));

        CREATE TABLE physical_worker_execution_lease (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            delivery_id uuid NOT NULL,
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            control_epoch integer NOT NULL CHECK (control_epoch >= 0),
            agent_code varchar(120) NOT NULL,
            worker_name varchar(160) NOT NULL,
            state varchar(32) NOT NULL CHECK (
                state IN ('PREPARING', 'ACTIVE', 'DRAINING', 'RELEASED')
            ),
            credential_sha256 varchar(64) NOT NULL CHECK (credential_sha256 ~ '^[0-9a-f]{64}$'),
            credential_expires_at timestamptz NOT NULL,
            prepared_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            activated_at timestamptz,
            draining_at timestamptz,
            released_at timestamptz,
            last_error varchar(1000),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, delivery_id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        CREATE UNIQUE INDEX uq_physical_worker_open_lease
            ON physical_worker_execution_lease (worker_name)
            WHERE state IN ('PREPARING', 'ACTIVE', 'DRAINING');
        CREATE UNIQUE INDEX uq_physical_worker_credential_digest
            ON physical_worker_execution_lease (credential_sha256);
        CREATE INDEX ix_physical_worker_lease_run
            ON physical_worker_execution_lease (tenant_id, run_id, state, updated_at);

        ALTER TABLE model_invocation
            DROP CONSTRAINT IF EXISTS model_invocation_status_check;
        UPDATE model_invocation SET status = 'REJECTED' WHERE status = 'REJECTED_PAUSED';
        ALTER TABLE model_invocation
            ADD COLUMN delivery_id uuid,
            ADD COLUMN dispatch_epoch integer,
            ADD COLUMN invocation_seq integer,
            ADD COLUMN delivery_status varchar(32) NOT NULL DEFAULT 'NOT_STARTED',
            ADD COLUMN terminal_seen_at timestamptz,
            ADD COLUMN usage_received_at timestamptz,
            ADD COLUMN budget_held_amount numeric(20,6) NOT NULL DEFAULT 0,
            ADD COLUMN failure_class varchar(64),
            ADD CONSTRAINT model_invocation_status_check CHECK (
                status IN ('STARTED', 'SUBMITTED', 'SETTLED', 'REJECTED', 'SUBMISSION_UNKNOWN')
            ),
            ADD CONSTRAINT model_invocation_delivery_status_check CHECK (
                delivery_status IN (
                    'NOT_STARTED', 'STREAMING', 'TERMINAL_SEEN', 'DELIVERED', 'DELIVERY_UNKNOWN'
                )
            ),
            ADD CONSTRAINT model_invocation_dispatch_epoch_check
                CHECK (dispatch_epoch IS NULL OR dispatch_epoch >= 0),
            ADD CONSTRAINT model_invocation_seq_check
                CHECK (invocation_seq IS NULL OR invocation_seq > 0),
            ADD CONSTRAINT model_invocation_budget_hold_check
                CHECK (budget_held_amount >= 0),
            ADD CONSTRAINT model_invocation_delivery_fk
                FOREIGN KEY (tenant_id, delivery_id)
                REFERENCES agentteams_task_delivery(tenant_id, id);
        CREATE UNIQUE INDEX uq_model_invocation_delivery_seq
            ON model_invocation (tenant_id, delivery_id, invocation_seq)
            WHERE delivery_id IS NOT NULL AND invocation_seq IS NOT NULL;
        CREATE INDEX ix_model_invocation_open_fingerprint
            ON model_invocation (tenant_id, delivery_id, request_sha256, status, delivery_status);

        CREATE TABLE model_usage_reconciliation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            delivery_id uuid NOT NULL,
            state varchar(32) NOT NULL CHECK (
                state IN ('PENDING', 'MATCHED', 'MISMATCH', 'UNKNOWN', 'POSTED')
            ),
            invocation_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            gateway_usage jsonb NOT NULL DEFAULT '{}'::jsonb,
            copaw_baseline jsonb,
            copaw_terminal jsonb,
            usage_record_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            difference_reason varchar(1000),
            reconciled_at timestamptz,
            posted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, delivery_id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, delivery_id) REFERENCES agentteams_task_delivery(tenant_id, id)
        );
        CREATE INDEX ix_model_usage_reconciliation_run
            ON model_usage_reconciliation (tenant_id, run_id, state, updated_at);
        """
    )
    tables = ("physical_worker_execution_lease", "model_usage_reconciliation")
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS model_usage_reconciliation CASCADE;
        DROP INDEX IF EXISTS uq_model_invocation_delivery_seq;
        DROP INDEX IF EXISTS ix_model_invocation_open_fingerprint;
        ALTER TABLE model_invocation
            DROP CONSTRAINT IF EXISTS model_invocation_delivery_fk,
            DROP CONSTRAINT IF EXISTS model_invocation_budget_hold_check,
            DROP CONSTRAINT IF EXISTS model_invocation_seq_check,
            DROP CONSTRAINT IF EXISTS model_invocation_dispatch_epoch_check,
            DROP CONSTRAINT IF EXISTS model_invocation_delivery_status_check,
            DROP CONSTRAINT IF EXISTS model_invocation_status_check,
            DROP COLUMN IF EXISTS failure_class,
            DROP COLUMN IF EXISTS budget_held_amount,
            DROP COLUMN IF EXISTS usage_received_at,
            DROP COLUMN IF EXISTS terminal_seen_at,
            DROP COLUMN IF EXISTS delivery_status,
            DROP COLUMN IF EXISTS invocation_seq,
            DROP COLUMN IF EXISTS dispatch_epoch,
            DROP COLUMN IF EXISTS delivery_id;
        UPDATE model_invocation SET status = 'REJECTED_PAUSED' WHERE status = 'REJECTED';
        ALTER TABLE model_invocation
            ADD CONSTRAINT model_invocation_status_check CHECK (
                status IN ('STARTED', 'SUBMITTED', 'SETTLED', 'SUBMISSION_UNKNOWN', 'REJECTED_PAUSED')
            );
        DROP TABLE IF EXISTS physical_worker_execution_lease CASCADE;
        ALTER TABLE agentteams_task_delivery
            DROP CONSTRAINT IF EXISTS agentteams_task_delivery_accounting_mode_check,
            DROP CONSTRAINT IF EXISTS agentteams_task_delivery_max_model_calls_check,
            DROP COLUMN IF EXISTS accounting_mode,
            DROP COLUMN IF EXISTS max_model_calls,
            DROP COLUMN IF EXISTS worker_name;
        """
    )
