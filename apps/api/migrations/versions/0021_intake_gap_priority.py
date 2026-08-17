"""Allow all six required intake gap questions to be persisted."""

from __future__ import annotations

from alembic import op

revision = "0021_intake_gap_priority"
down_revision = "0020_adr0009_ticket_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE intake_gap_question
            DROP CONSTRAINT intake_gap_question_priority_check;
        ALTER TABLE intake_gap_question
            ADD CONSTRAINT intake_gap_question_priority_check
            CHECK (priority BETWEEN 1 AND 6);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE intake_gap_question
            DROP CONSTRAINT intake_gap_question_priority_check;
        ALTER TABLE intake_gap_question
            ADD CONSTRAINT intake_gap_question_priority_check
            CHECK (priority BETWEEN 1 AND 5);
        """
    )
