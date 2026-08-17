"""ADR 0004: durable Agent-initiated InformationRequest and its answer.

The clarification loop must survive a restart, so ``WAITING_FOR_USER`` is
derived from durable rows rather than from an in-memory prompt.  Answers are
append-only: a re-answer supersedes the previous row instead of mutating it, so
the audit trail keeps every value the user supplied.
"""

from __future__ import annotations

from alembic import op

from migrations._common import (
    enable_tenant_rls,
    grant_runtime,
    install_append_only_trigger,
    install_updated_at_trigger,
)

revision = "0015_adr0004_information_request"
down_revision = "0014_v02_publisher_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A clarification is only meaningful inside one Run.  Referencing the Task by
    # (tenant_id, id) alone would let a row point at a Task from another Run, so a
    # Run-scoped uniqueness target is added first and referenced composite-wise below.
    op.execute(
        """
        ALTER TABLE task ADD CONSTRAINT ux_task_tenant_run_id UNIQUE (tenant_id, run_id, id);

        ALTER TABLE task
            ADD COLUMN dispatch_epoch integer NOT NULL DEFAULT 0
                CONSTRAINT task_dispatch_epoch_non_negative CHECK (dispatch_epoch >= 0);
        """
    )
    op.execute(
        """
        CREATE TABLE information_request (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            agent_identity_ref varchar(200) NOT NULL,
            profile_field varchar(120) NOT NULL,
            question varchar(1000) NOT NULL,
            why_blocking varchar(1000) NOT NULL,
            impact_dimension varchar(64) NOT NULL,
            answer_kind varchar(32) NOT NULL
                CHECK (answer_kind IN ('PROFILE_FIELD', 'EVIDENCE')),
            status varchar(32) NOT NULL
                CHECK (status IN ('OPEN', 'ANSWERED', 'CANCELLED')),
            answered_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id, task_id)
                REFERENCES task(tenant_id, run_id, id),
            CONSTRAINT information_request_answer_consistent
                CHECK ((status = 'ANSWERED') = (answered_at IS NOT NULL))
        );

        CREATE UNIQUE INDEX ux_information_request_open_field
            ON information_request (tenant_id, task_id, profile_field)
            WHERE status = 'OPEN';

        CREATE INDEX ix_information_request_open_run
            ON information_request (tenant_id, run_id)
            WHERE status = 'OPEN';

        CREATE TABLE information_request_answer (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            information_request_id uuid NOT NULL,
            run_id uuid NOT NULL,
            answer_text varchar(4000) NOT NULL,
            answer_sha256 varchar(64) NOT NULL CHECK (answer_sha256 ~ '^[0-9a-f]{64}$'),
            profile_revision integer,
            evidence_id uuid,
            supersedes_id uuid,
            answered_by varchar(255) NOT NULL,
            correlation_id varchar(200) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            submission_sha256 varchar(64) NOT NULL
                CHECK (submission_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id, information_request_id)
                REFERENCES information_request(tenant_id, run_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, evidence_id) REFERENCES evidence(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_id)
                REFERENCES information_request_answer(tenant_id, id)
        );

        CREATE INDEX ix_information_request_answer_request
            ON information_request_answer (tenant_id, information_request_id);

        -- One submission answers many requests, so the Idempotency-Key is unique
        -- per request inside a Run: a replay collides, a new question does not.
        CREATE UNIQUE INDEX ux_information_request_answer_idempotency
            ON information_request_answer
            (tenant_id, run_id, idempotency_key, information_request_id);

        CREATE TABLE clarification_impact_assessment (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            assessed_by_agent_ref varchar(200) NOT NULL,
            answered_request_ids jsonb NOT NULL,
            affected_task_ids jsonb NOT NULL,
            unaffected_task_ids jsonb NOT NULL,
            rationale varchar(2000) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE INDEX ix_clarification_impact_run
            ON clarification_impact_assessment (tenant_id, run_id);
        """
    )
    enable_tenant_rls(
        op,
        (
            "information_request",
            "information_request_answer",
            "clarification_impact_assessment",
        ),
    )
    grant_runtime(
        op,
        (
            "information_request",
            "information_request_answer",
            "clarification_impact_assessment",
        ),
    )
    install_updated_at_trigger(op, "information_request")
    install_append_only_trigger(op, "information_request_answer")
    install_append_only_trigger(op, "clarification_impact_assessment")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS clarification_impact_assessment CASCADE;")
    op.execute("DROP TABLE IF EXISTS information_request_answer CASCADE;")
    op.execute("DROP TABLE IF EXISTS information_request CASCADE;")
    op.execute("ALTER TABLE task DROP COLUMN IF EXISTS dispatch_epoch;")
    op.execute("ALTER TABLE task DROP CONSTRAINT IF EXISTS ux_task_tenant_run_id;")
