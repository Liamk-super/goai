"""ADR 0009: durable native AgentTeams Team Leader plans and room deliveries."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime

revision = "0019_adr0009_native_leader"
down_revision = "0018_adr0008_uvd_dual_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_plan (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            planning_task_id uuid NOT NULL,
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            plan_version integer NOT NULL CHECK (plan_version >= 1),
            evaluation_mode varchar(48) NOT NULL,
            raw_plan jsonb NOT NULL,
            plan_sha256 varchar(64) NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
            status varchar(32) NOT NULL,
            matrix_event_id varchar(255),
            rejection_code varchar(64),
            decision_reason varchar(2000),
            supersedes_plan_id uuid,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            decided_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, plan_version),
            UNIQUE (tenant_id, run_id, plan_sha256),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, planning_task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_plan_id) REFERENCES agent_plan(tenant_id, id),
            CHECK (
                (status = 'PROPOSED' AND decided_at IS NULL)
                OR (status IN ('ACCEPTED', 'REJECTED', 'SUPERSEDED') AND decided_at IS NOT NULL)
            )
        );
        CREATE INDEX ix_agent_plan_run_status
            ON agent_plan (tenant_id, run_id, status, plan_version DESC);
        CREATE UNIQUE INDEX uq_agent_plan_one_accepted
            ON agent_plan (tenant_id, run_id)
            WHERE status = 'ACCEPTED';

        CREATE TABLE manager_synthesis (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            deterministic_candidate varchar(40) NOT NULL,
            proposed_recommendation varchar(40) NOT NULL,
            raw_synthesis jsonb NOT NULL,
            synthesis_sha256 varchar(64) NOT NULL CHECK (synthesis_sha256 ~ '^[0-9a-f]{64}$'),
            status varchar(32) NOT NULL,
            approval_request_id uuid,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, synthesis_sha256),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, approval_request_id) REFERENCES approval_request(tenant_id, id)
        );
        CREATE INDEX ix_manager_synthesis_run
            ON manager_synthesis (tenant_id, run_id, created_at DESC);

        CREATE TABLE agent_task_ticket (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            plan_id uuid NOT NULL,
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            target_agent varchar(120) NOT NULL,
            ticket_sha256 varchar(64) NOT NULL CHECK (ticket_sha256 ~ '^[0-9a-f]{64}$'),
            public_summary jsonb NOT NULL,
            usage_baseline jsonb,
            status varchar(32) NOT NULL CHECK (status IN ('PREPARED', 'DELIVERED', 'EXPIRED')),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            delivered_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, task_id, dispatch_epoch),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, plan_id) REFERENCES agent_plan(tenant_id, id),
            CHECK ((status = 'DELIVERED') = (delivered_at IS NOT NULL))
        );
        CREATE INDEX ix_agent_task_ticket_expiry
            ON agent_task_ticket (tenant_id, status, expires_at);

        ALTER TABLE agentteams_task_delivery ADD COLUMN plan_id uuid;
        ALTER TABLE agentteams_task_delivery ADD COLUMN delegated_by varchar(120);
        ALTER TABLE agentteams_task_delivery ADD COLUMN team_room_event_id varchar(255);
        ALTER TABLE agentteams_task_delivery ADD COLUMN worker_room_id varchar(255);
        ALTER TABLE agentteams_task_delivery ADD COLUMN worker_room_event_id varchar(255);
        ALTER TABLE agentteams_task_delivery
            ADD CONSTRAINT fk_agentteams_task_delivery_plan
            FOREIGN KEY (tenant_id, plan_id) REFERENCES agent_plan(tenant_id, id);
        CREATE INDEX ix_agentteams_delivery_plan
            ON agentteams_task_delivery (tenant_id, plan_id, task_id);

        ALTER TABLE matrix_event_receipt
            ADD COLUMN event_kind varchar(32) NOT NULL DEFAULT 'RESULT';
        ALTER TABLE matrix_event_receipt ADD COLUMN recipient_mxid varchar(255);
        CREATE INDEX ix_matrix_event_receipt_timeline
            ON matrix_event_receipt (tenant_id, run_id, created_at, event_kind);
        """
    )
    enable_tenant_rls(op, ("agent_plan", "manager_synthesis", "agent_task_ticket"))
    grant_runtime(op, ("agent_plan", "manager_synthesis", "agent_task_ticket"))


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_matrix_event_receipt_timeline;
        ALTER TABLE matrix_event_receipt DROP COLUMN IF EXISTS recipient_mxid;
        ALTER TABLE matrix_event_receipt DROP COLUMN IF EXISTS event_kind;

        DROP INDEX IF EXISTS ix_agentteams_delivery_plan;
        ALTER TABLE agentteams_task_delivery DROP CONSTRAINT IF EXISTS fk_agentteams_task_delivery_plan;
        ALTER TABLE agentteams_task_delivery DROP COLUMN IF EXISTS worker_room_event_id;
        ALTER TABLE agentteams_task_delivery DROP COLUMN IF EXISTS worker_room_id;
        ALTER TABLE agentteams_task_delivery DROP COLUMN IF EXISTS team_room_event_id;
        ALTER TABLE agentteams_task_delivery DROP COLUMN IF EXISTS delegated_by;
        ALTER TABLE agentteams_task_delivery DROP COLUMN IF EXISTS plan_id;

        DROP TABLE IF EXISTS agent_task_ticket CASCADE;
        DROP TABLE IF EXISTS manager_synthesis CASCADE;
        DROP TABLE IF EXISTS agent_plan CASCADE;
        """
    )
