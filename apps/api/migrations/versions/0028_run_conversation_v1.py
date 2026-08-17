"""Add durable controlled Run conversation messages."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0028_run_conversation_v1"
down_revision = "0027_pause_delivery_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE run_conversation_message (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            channel varchar(40) NOT NULL CHECK (
                channel IN ('supervisor', 'user-evidence', 'product-engineering', 'business-investment')
            ),
            role varchar(16) NOT NULL CHECK (role IN ('USER', 'SUPERVISOR', 'AGENT', 'SYSTEM')),
            message_kind varchar(32) NOT NULL CHECK (
                message_kind IN ('MESSAGE', 'ROUTING_RECEIPT', 'QUESTION', 'ANSWER')
            ),
            object_key varchar(1024) NOT NULL CHECK (object_key !~ '(^/|(^|/)\.\.(/|$))'),
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            idempotency_key varchar(200) NOT NULL,
            correlation_id uuid NOT NULL,
            route_state varchar(32) NOT NULL CHECK (
                route_state IN ('RECORDED', 'ROUTED', 'WAITING_FOR_USER', 'NEEDS_ATTENTION')
            ),
            affected_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            response jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_by varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE INDEX ix_run_conversation_timeline
            ON run_conversation_message (tenant_id, run_id, created_at, id);
        """
    )
    enable_tenant_rls(op, ("run_conversation_message",))
    grant_runtime(op, ("run_conversation_message",))
    install_append_only_trigger(op, "run_conversation_message")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_conversation_message CASCADE;")
