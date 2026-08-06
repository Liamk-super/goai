"""Add recoverable Outbox claims and immutable AgentTeams/Matrix bindings."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0013_v02_async_agentteams"
down_revision = "0012_t12_seed_p0_skills"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE outbox_message ADD COLUMN claimed_by varchar(160);
        ALTER TABLE outbox_message ADD COLUMN claimed_at timestamptz;
        CREATE INDEX ix_outbox_message_stale_claim
            ON outbox_message (tenant_id, claimed_at)
            WHERE publish_status = 'CLAIMED';

        CREATE TABLE agentteams_run_binding (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            agentteams_version varchar(32) NOT NULL,
            team_name varchar(160) NOT NULL,
            team_room_id varchar(255),
            leader_room_id varchar(255),
            binding_status varchar(32) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE TABLE matrix_event_receipt (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid,
            room_id varchar(255) NOT NULL,
            matrix_event_id varchar(255) NOT NULL,
            sender_mxid varchar(255) NOT NULL,
            payload_sha256 varchar(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
            processing_status varchar(32) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, matrix_event_id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        """
    )
    enable_tenant_rls(op, ("agentteams_run_binding", "matrix_event_receipt"))
    grant_runtime(op, ("agentteams_run_binding", "matrix_event_receipt"))
    install_append_only_trigger(op, "matrix_event_receipt")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS matrix_event_receipt CASCADE;")
    op.execute("DROP TABLE IF EXISTS agentteams_run_binding CASCADE;")
    op.execute("ALTER TABLE outbox_message DROP COLUMN IF EXISTS claimed_at;")
    op.execute("ALTER TABLE outbox_message DROP COLUMN IF EXISTS claimed_by;")
