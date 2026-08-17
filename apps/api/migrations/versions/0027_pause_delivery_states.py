"""Allow durable pause states in AgentTeams task deliveries."""

from __future__ import annotations

from alembic import op

revision = "0027_pause_delivery_states"
down_revision = "0026_run_execution_pause"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agentteams_task_delivery
            DROP CONSTRAINT IF EXISTS agentteams_task_delivery_status_check;
        ALTER TABLE agentteams_task_delivery
            DROP CONSTRAINT IF EXISTS agentteams_task_delivery_check1;
        ALTER TABLE agentteams_task_delivery
            ADD CONSTRAINT agentteams_task_delivery_status_check
            CHECK (status IN (
                'DELIVERED', 'COMPLETED', 'TIMED_OUT', 'PAUSE_STOP_PENDING', 'PAUSED'
            ));
        ALTER TABLE agentteams_task_delivery
            ADD CONSTRAINT agentteams_task_delivery_completion_check
            CHECK (
                (status IN ('DELIVERED', 'PAUSE_STOP_PENDING')) = (completed_at IS NULL)
            );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE agentteams_task_delivery
            DROP CONSTRAINT IF EXISTS agentteams_task_delivery_completion_check;
        ALTER TABLE agentteams_task_delivery
            DROP CONSTRAINT IF EXISTS agentteams_task_delivery_status_check;
        ALTER TABLE agentteams_task_delivery
            ADD CONSTRAINT agentteams_task_delivery_status_check
            CHECK (status IN ('DELIVERED', 'COMPLETED', 'TIMED_OUT'));
        ALTER TABLE agentteams_task_delivery
            ADD CONSTRAINT agentteams_task_delivery_check1
            CHECK ((status = 'DELIVERED') = (completed_at IS NULL));
        """
    )
