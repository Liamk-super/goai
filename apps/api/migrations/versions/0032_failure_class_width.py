"""Align persisted Task failure classes with the v4 handoff contract."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_failure_class_width"
down_revision = "0031_report_v22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "task",
        "last_failure_class",
        existing_type=sa.String(length=40),
        type_=sa.String(length=120),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "task",
        "last_failure_class",
        existing_type=sa.String(length=120),
        type_=sa.String(length=40),
        existing_nullable=True,
        postgresql_using="left(last_failure_class, 40)",
    )
