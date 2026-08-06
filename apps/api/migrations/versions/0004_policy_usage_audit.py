"""Create policy, usage, audit, trace and reliable-message boundaries.

Forward: adds tenant-scoped operational metadata and the transactional
Outbox/Inbox tables.  Rollback removes only these new tables.  Message payloads
are structured envelopes; the application adapter rejects chat transcripts and
private reasoning fields before they can enter this table.
"""

from __future__ import annotations

from alembic import op

from migrations._common import (
    enable_tenant_rls,
    grant_runtime,
    install_append_only_trigger,
)

revision = "0004_policy_usage_audit"
down_revision = "0003_evidence_decision"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "memory_candidate",
    "memory_item",
    "approval_request",
    "usage_record",
    "audit_event",
    "trace_metadata",
    "outbox_message",
    "inbox_message",
    "event_delivery_attempt",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE memory_candidate (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            source_finding_id uuid,
            status varchar(32) NOT NULL DEFAULT 'PROPOSED',
            candidate jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
            FOREIGN KEY (tenant_id, source_finding_id) REFERENCES finding(tenant_id, id)
        );
        CREATE INDEX ix_memory_candidate_tenant_project ON memory_candidate (tenant_id, project_id, created_at);

        CREATE TABLE memory_item (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            item_type varchar(64) NOT NULL,
            validity_status varchar(32) NOT NULL DEFAULT 'ACTIVE',
            valid_until timestamptz,
            content jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id)
        );
        CREATE INDEX ix_memory_item_tenant_project_validity
            ON memory_item (tenant_id, project_id, validity_status, valid_until);

        CREATE TABLE approval_request (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            tool_code varchar(120) NOT NULL,
            parameters_sha256 varchar(64) NOT NULL CHECK (parameters_sha256 ~ '^[0-9a-fA-F]{64}$'),
            status varchar(32) NOT NULL DEFAULT 'PENDING',
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE INDEX ix_approval_request_tenant_run_status ON approval_request (tenant_id, run_id, status, expires_at);

        CREATE TABLE usage_record (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid,
            category varchar(100) NOT NULL,
            quantity numeric(20,6) NOT NULL CHECK (quantity >= 0),
            cost numeric(20,6) NOT NULL CHECK (cost >= 0),
            idempotency_key varchar(200) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        CREATE INDEX ix_usage_record_tenant_run_time ON usage_record (tenant_id, run_id, created_at);

        CREATE TABLE audit_event (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid,
            actor_type varchar(64) NOT NULL,
            action varchar(120) NOT NULL,
            outcome varchar(32) NOT NULL,
            payload_sha256 varchar(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-fA-F]{64}$'),
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE INDEX ix_audit_event_tenant_run_time ON audit_event (tenant_id, run_id, occurred_at);

        CREATE TABLE trace_metadata (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid,
            stage_id uuid,
            task_id uuid,
            correlation_id uuid NOT NULL,
            span_id varchar(128) NOT NULL,
            attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
            payload_sha256 varchar(64) CHECK (payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-fA-F]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, stage_id) REFERENCES stage(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        CREATE INDEX ix_trace_metadata_tenant_run_time ON trace_metadata (tenant_id, run_id, created_at);

        CREATE TABLE outbox_message (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            aggregate_id uuid NOT NULL,
            aggregate_type varchar(120) NOT NULL,
            event_type varchar(160) NOT NULL,
            event_id uuid NOT NULL,
            schema_version varchar(20) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            publish_status varchar(32) NOT NULL DEFAULT 'PENDING',
            available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            published_at timestamptz,
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            last_error varchar(2000),
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            UNIQUE (tenant_id, event_id)
        );
        CREATE INDEX ix_outbox_message_publishable
            ON outbox_message (tenant_id, publish_status, available_at, created_at);

        CREATE TABLE inbox_message (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            outbox_message_id uuid,
            consumer_name varchar(120) NOT NULL,
            dedupe_key varchar(200) NOT NULL,
            event_id uuid NOT NULL,
            event_type varchar(160) NOT NULL,
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            processing_status varchar(32) NOT NULL DEFAULT 'PROCESSING',
            received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            processed_at timestamptz,
            last_error varchar(2000),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, consumer_name, dedupe_key),
            FOREIGN KEY (tenant_id, outbox_message_id) REFERENCES outbox_message(tenant_id, id)
        );
        CREATE INDEX ix_inbox_message_tenant_status
            ON inbox_message (tenant_id, consumer_name, processing_status, created_at);

        CREATE TABLE event_delivery_attempt (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            outbox_message_id uuid NOT NULL,
            attempt_no integer NOT NULL CHECK (attempt_no > 0),
            status varchar(32) NOT NULL,
            error varchar(2000),
            attempted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, outbox_message_id, attempt_no),
            FOREIGN KEY (tenant_id, outbox_message_id) REFERENCES outbox_message(tenant_id, id)
        );
        """
    )
    enable_tenant_rls(op, _TENANT_TABLES)
    grant_runtime(op, _TENANT_TABLES)
    for table in ("audit_event", "trace_metadata"):
        install_append_only_trigger(op, table)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
