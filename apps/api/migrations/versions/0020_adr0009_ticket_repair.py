"""Reconcile the durable ADR 0009 Agent Task ticket table."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime

revision = "0020_adr0009_ticket_repair"
down_revision = "0019_adr0009_native_leader"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_task_ticket (
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
        CREATE INDEX IF NOT EXISTS ix_agent_task_ticket_expiry
            ON agent_task_ticket (tenant_id, status, expires_at);
        """
    )
    enable_tenant_rls(op, ("agent_task_ticket",))
    grant_runtime(op, ("agent_task_ticket",))


def downgrade() -> None:
    pass
