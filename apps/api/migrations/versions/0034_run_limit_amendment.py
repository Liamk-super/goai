"""ADR 0022: append-only Demo Run limit amendments and exact-result replay."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0034_run_limit_amendment"
down_revision = "0033_report_revision_width"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_limit_amendment (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            amendment_version integer NOT NULL CHECK (amendment_version > 0),
            dispatch_epoch integer NOT NULL CHECK (dispatch_epoch >= 0),
            control_epoch integer NOT NULL CHECK (control_epoch >= 0),
            matrix_event_id varchar(255) NOT NULL,
            matrix_payload_sha256 varchar(64) NOT NULL CHECK (matrix_payload_sha256 ~ '^[0-9a-f]{64}$'),
            model_calls integer NOT NULL CHECK (model_calls > 0 AND model_calls <= 4096),
            input_tokens integer NOT NULL CHECK (input_tokens > 0 AND input_tokens <= 200000000),
            output_tokens integer NOT NULL CHECK (output_tokens > 0 AND output_tokens <= 20000000),
            reason varchar(1000) NOT NULL,
            authorized_by varchar(255) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            correlation_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, amendment_version),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );

        CREATE TABLE run_limit_amendment_replay (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            amendment_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            matrix_event_id varchar(255) NOT NULL,
            matrix_payload_sha256 varchar(64) NOT NULL CHECK (matrix_payload_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, amendment_id),
            FOREIGN KEY (tenant_id, amendment_id) REFERENCES run_limit_amendment(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );

        CREATE INDEX ix_run_limit_amendment_event
            ON run_limit_amendment (tenant_id, run_id, task_id, matrix_event_id);
        CREATE INDEX ix_run_limit_amendment_replay_event
            ON run_limit_amendment_replay (tenant_id, run_id, task_id, matrix_event_id);
        """
    )
    tables = ("run_limit_amendment", "run_limit_amendment_replay")
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)
    for table in tables:
        install_append_only_trigger(op, table)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS run_limit_amendment_replay CASCADE;
        DROP TABLE IF EXISTS run_limit_amendment CASCADE;
        """
    )
