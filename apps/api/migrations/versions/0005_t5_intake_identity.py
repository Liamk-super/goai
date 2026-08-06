"""Add durable T5 membership, intake draft, and correlation-bound questions.

This is an additive migration: confirmed ProductProfile facts remain in the
existing append-only table, while model inference and unanswered questions are
kept separately and cannot be mistaken for confirmed facts.
"""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime

revision = "0005_t5_intake_identity"
down_revision = "0004_policy_usage_audit"
branch_labels = None
depends_on = None

_TABLES = ("workspace_member", "product_profile_draft", "intake_gap_question")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace_member (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            actor_id varchar(255) NOT NULL,
            role varchar(32) NOT NULL CHECK (role IN ('OWNER', 'EDITOR', 'VIEWER')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, workspace_id, actor_id),
            FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspace(tenant_id, id)
        );
        CREATE INDEX ix_workspace_member_tenant_actor ON workspace_member (tenant_id, actor_id, workspace_id);

        CREATE TABLE product_profile_draft (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            source varchar(32) NOT NULL CHECK (source = 'MODEL_INFERENCE'),
            inferred_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
            answered_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
            status varchar(32) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONFIRMED')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            confirmed_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, product_version_id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );
        CREATE INDEX ix_product_profile_draft_tenant_version ON product_profile_draft (tenant_id, product_version_id);

        CREATE TABLE intake_gap_question (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            draft_id uuid NOT NULL,
            correlation_id uuid NOT NULL,
            field varchar(100) NOT NULL,
            question varchar(2000) NOT NULL,
            priority smallint NOT NULL CHECK (priority BETWEEN 1 AND 5),
            answer text,
            answered_by varchar(255),
            answered_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, product_version_id, field),
            UNIQUE (tenant_id, product_version_id, priority),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, draft_id) REFERENCES product_profile_draft(tenant_id, id)
        );
        CREATE INDEX ix_intake_gap_question_tenant_version
            ON intake_gap_question (tenant_id, product_version_id, priority);
        """
    )
    enable_tenant_rls(op, _TABLES)
    grant_runtime(op, _TABLES)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
