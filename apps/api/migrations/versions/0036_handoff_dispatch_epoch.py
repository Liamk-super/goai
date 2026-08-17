"""Keep Matrix handoff projections distinct across Task re-dispatches."""

from __future__ import annotations

from alembic import op

revision = "0036_handoff_dispatch_epoch"
down_revision = "0035_canonical_event_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE matrix_handoff
            ADD COLUMN dispatch_epoch integer NOT NULL DEFAULT 0;

        ALTER TABLE matrix_handoff
            DROP CONSTRAINT matrix_handoff_tenant_id_run_id_task_id_sender_agent_kind_key;

        ALTER TABLE matrix_handoff
            ADD CONSTRAINT uq_matrix_handoff_dispatch_epoch
            UNIQUE (tenant_id, run_id, task_id, sender_agent, kind, dispatch_epoch);

        ALTER TABLE matrix_handoff
            ADD CONSTRAINT matrix_handoff_dispatch_epoch_check CHECK (dispatch_epoch >= 0);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE matrix_handoff
            DROP CONSTRAINT IF EXISTS matrix_handoff_dispatch_epoch_check;
        ALTER TABLE matrix_handoff
            DROP CONSTRAINT IF EXISTS uq_matrix_handoff_dispatch_epoch;
        ALTER TABLE matrix_handoff
            ADD CONSTRAINT matrix_handoff_tenant_id_run_id_task_id_sender_agent_kind_key
            UNIQUE (tenant_id, run_id, task_id, sender_agent, kind);
        ALTER TABLE matrix_handoff
            DROP COLUMN dispatch_epoch;
        """
    )
