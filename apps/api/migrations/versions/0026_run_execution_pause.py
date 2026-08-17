"""ADR 0014: durable Run execution control and model admission ledger."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0026_run_execution_pause"
down_revision = "0025_adr0012_agent_report"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_execution_control (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            state varchar(32) NOT NULL CHECK (
                state IN ('ACTIVE', 'PAUSE_REQUESTED', 'PAUSED', 'PAUSE_BLOCKED', 'CLOSED')
            ),
            control_epoch integer NOT NULL DEFAULT 0 CHECK (control_epoch >= 0),
            requested_by varchar(255),
            pause_reason varchar(64),
            usage_settlement_status varchar(32) NOT NULL DEFAULT 'NONE' CHECK (
                usage_settlement_status IN ('NONE', 'PENDING', 'SETTLED', 'UNKNOWN')
            ),
            in_flight_count integer NOT NULL DEFAULT 0 CHECK (in_flight_count >= 0),
            pause_requested_at timestamptz,
            paused_at timestamptz,
            resumed_at timestamptz,
            closed_at timestamptz,
            last_error varchar(1000),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE TABLE run_control_request (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            operation varchar(16) NOT NULL CHECK (operation IN ('PAUSE', 'RESUME')),
            idempotency_key varchar(200) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            response jsonb NOT NULL,
            correlation_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE TABLE run_execution_checkpoint (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            control_epoch integer NOT NULL CHECK (control_epoch > 0),
            interrupted_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            completed_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            usage_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, control_epoch),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE TABLE run_execution_event (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            event_type varchar(64) NOT NULL CHECK (
                event_type IN ('run.pause_requested', 'run.paused', 'run.pause_blocked', 'run.resumed')
            ),
            control_state varchar(32) NOT NULL,
            control_epoch integer NOT NULL CHECK (control_epoch >= 0),
            data jsonb NOT NULL DEFAULT '{}'::jsonb,
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE TABLE model_invocation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            agent_code varchar(120) NOT NULL,
            control_epoch integer NOT NULL CHECK (control_epoch >= 0),
            model varchar(255) NOT NULL,
            status varchar(32) NOT NULL CHECK (
                status IN ('STARTED', 'SUBMITTED', 'SETTLED', 'SUBMISSION_UNKNOWN', 'REJECTED_PAUSED')
            ),
            upstream_request_id varchar(255),
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            prompt_tokens integer CHECK (prompt_tokens >= 0),
            completion_tokens integer CHECK (completion_tokens >= 0),
            cost numeric(20,6),
            started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            submitted_at timestamptz,
            settled_at timestamptz,
            last_error varchar(1000),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );

        CREATE INDEX ix_run_execution_event_stream
            ON run_execution_event (tenant_id, run_id, occurred_at, id);
        CREATE INDEX ix_model_invocation_active
            ON model_invocation (tenant_id, run_id, status, started_at);

        INSERT INTO run_execution_control (
            tenant_id, run_id, state, control_epoch, usage_settlement_status,
            closed_at, created_at, updated_at
        )
        SELECT
            tenant_id,
            id,
            CASE WHEN status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED') THEN 'CLOSED' ELSE 'ACTIVE' END,
            0,
            'NONE',
            CASE WHEN status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED') THEN updated_at ELSE NULL END,
            created_at,
            updated_at
        FROM evaluation_run;

        CREATE FUNCTION launchscope_sync_run_execution_control() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO run_execution_control (
                    tenant_id, run_id, state, usage_settlement_status, closed_at, created_at, updated_at
                ) VALUES (
                    NEW.tenant_id,
                    NEW.id,
                    CASE
                        WHEN NEW.status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED') THEN 'CLOSED'
                        ELSE 'ACTIVE'
                    END,
                    'NONE',
                    CASE
                        WHEN NEW.status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED') THEN NEW.updated_at
                        ELSE NULL
                    END,
                    NEW.created_at,
                    NEW.updated_at
                );
            ELSIF NEW.status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')
                AND OLD.status IS DISTINCT FROM NEW.status THEN
                UPDATE run_execution_control
                SET state = 'CLOSED', closed_at = NEW.updated_at, updated_at = NEW.updated_at
                WHERE tenant_id = NEW.tenant_id AND run_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER evaluation_run_sync_execution_control
        AFTER INSERT OR UPDATE OF status ON evaluation_run
        FOR EACH ROW EXECUTE FUNCTION launchscope_sync_run_execution_control();
        """
    )
    tables = (
        "run_execution_control",
        "run_control_request",
        "run_execution_checkpoint",
        "run_execution_event",
        "model_invocation",
    )
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)
    install_append_only_trigger(op, "run_control_request")
    install_append_only_trigger(op, "run_execution_checkpoint")
    install_append_only_trigger(op, "run_execution_event")


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS evaluation_run_sync_execution_control ON evaluation_run;
        DROP FUNCTION IF EXISTS launchscope_sync_run_execution_control();
        DROP TABLE IF EXISTS model_invocation CASCADE;
        DROP TABLE IF EXISTS run_execution_event CASCADE;
        DROP TABLE IF EXISTS run_execution_checkpoint CASCADE;
        DROP TABLE IF EXISTS run_control_request CASCADE;
        DROP TABLE IF EXISTS run_execution_control CASCADE;
        """
    )
