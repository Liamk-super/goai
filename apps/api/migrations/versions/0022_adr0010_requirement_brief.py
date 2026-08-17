"""ADR 0010: durable requirement briefs and the supervisor chat boundary."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime

revision = "0022_adr0010_requirement_brief"
down_revision = "0021_intake_gap_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE requirement_brief (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            revision integer NOT NULL CHECK (revision >= 1),
            schema_version varchar(16) NOT NULL CHECK (schema_version = '1.0'),
            raw_input_object_key varchar(1024) NOT NULL,
            raw_input_sha256 varchar(64) NOT NULL CHECK (raw_input_sha256 ~ '^[0-9a-f]{64}$'),
            document jsonb NOT NULL,
            confirmation_required boolean NOT NULL,
            status varchar(32) NOT NULL CHECK (status IN ('WAITING_FOR_USER', 'READY_FOR_PLANNING', 'SUPERSEDED')),
            created_by varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            confirmed_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, product_version_id, revision),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            CHECK ((status = 'WAITING_FOR_USER') = (confirmation_required AND confirmed_at IS NULL))
        );
        CREATE INDEX ix_requirement_brief_latest
            ON requirement_brief (tenant_id, product_version_id, revision DESC);

        CREATE TABLE supervisor_chat_message (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            brief_id uuid,
            role varchar(16) NOT NULL CHECK (role IN ('USER', 'SUPERVISOR')),
            message_kind varchar(32) NOT NULL CHECK (
                message_kind IN ('REQUIREMENT', 'CLARIFICATION', 'SUPPLEMENT', 'CHANGE', 'PROGRESS', 'CONFIRMATION')
            ),
            object_key varchar(1024) NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            idempotency_key varchar(200) NOT NULL,
            correlation_id uuid NOT NULL,
            interaction_state varchar(32) NOT NULL CHECK (
                interaction_state IN ('INTAKE_NORMALIZING', 'WAITING_FOR_USER', 'LEADER_PLANNING', 'NEEDS_ATTENTION')
            ),
            created_by varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, brief_id) REFERENCES requirement_brief(tenant_id, id)
        );
        CREATE INDEX ix_supervisor_chat_timeline
            ON supervisor_chat_message (tenant_id, product_version_id, created_at, id);

        CREATE TABLE requirement_change (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            brief_id uuid NOT NULL,
            document jsonb NOT NULL,
            status varchar(32) NOT NULL CHECK (status IN ('PROPOSED', 'CONFIRMED', 'APPLIED', 'REJECTED')),
            created_by varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, brief_id) REFERENCES requirement_brief(tenant_id, id)
        );
        CREATE INDEX ix_requirement_change_run
            ON requirement_change (tenant_id, run_id, created_at DESC);
        """
    )
    enable_tenant_rls(op, ("requirement_brief", "supervisor_chat_message", "requirement_change"))
    grant_runtime(op, ("requirement_brief", "supervisor_chat_message", "requirement_change"))


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS requirement_change CASCADE;
        DROP TABLE IF EXISTS supervisor_chat_message CASCADE;
        DROP TABLE IF EXISTS requirement_brief CASCADE;
        """
    )
