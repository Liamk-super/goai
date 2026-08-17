"""Allow immutable Agent reports to retain every recovery revision."""

from __future__ import annotations

from alembic import op

revision = "0033_report_revision_width"
down_revision = "0032_failure_class_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "agent_report_artifact_revision_check",
        "agent_report_artifact",
        type_="check",
    )
    op.create_check_constraint(
        "agent_report_artifact_revision_check",
        "agent_report_artifact",
        "revision >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "agent_report_artifact_revision_check",
        "agent_report_artifact",
        type_="check",
    )
    op.create_check_constraint(
        "agent_report_artifact_revision_check",
        "agent_report_artifact",
        "revision BETWEEN 0 AND 2",
    )
