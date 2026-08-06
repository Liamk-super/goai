"""Persist body-free Agent/Matrix handoff metadata for the T12 vertical slice."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0011_t12_execution_evidence"
down_revision = "0010_t11_budget_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE matrix_handoff (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            room_id varchar(255) NOT NULL,
            sender_agent varchar(120) NOT NULL,
            receiver_agent varchar(120) NOT NULL,
            kind varchar(40) NOT NULL,
            finding_id uuid,
            evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            risk varchar(32) NOT NULL,
            confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            approval_required boolean NOT NULL DEFAULT false,
            payload_sha256 varchar(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, task_id, sender_agent, kind),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, finding_id) REFERENCES finding(tenant_id, id)
        );
        CREATE INDEX ix_matrix_handoff_tenant_run_time ON matrix_handoff (tenant_id, run_id, created_at);
        """
    )
    enable_tenant_rls(op, ("matrix_handoff",))
    grant_runtime(op, ("matrix_handoff",))
    install_append_only_trigger(op, "matrix_handoff")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS matrix_handoff CASCADE;")
