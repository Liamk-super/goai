"""Create immutable run manifest and mutable stage/task execution metadata.

Forward: expands the dossier boundary with the execution graph and frozen
configuration.  Rollback removes only this revision after later revisions are
downgraded.  Run status history is append-only; current status remains on the
run row for efficient guarded reads.
"""

from __future__ import annotations

from alembic import op

from migrations._common import (
    enable_tenant_rls,
    grant_runtime,
    install_updated_at_trigger,
)

revision = "0002_evaluation_manifest"
down_revision = "0001_identity_dossier"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "evaluation_run",
    "run_manifest",
    "stage",
    "task",
    "task_dependency",
    "run_status_history",
    "skill_invocation",
    "tool_invocation",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_identity (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            code varchar(120) NOT NULL UNIQUE,
            version varchar(20) NOT NULL,
            capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
            allowed_actions jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );

        CREATE TABLE skill_version (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            skill_code varchar(120) NOT NULL,
            version varchar(20) NOT NULL,
            manifest_sha256 varchar(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-fA-F]{64}$'),
            input_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
            output_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (skill_code, version)
        );

        CREATE TABLE evaluation_run (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            status varchar(40) NOT NULL,
            current_stage varchar(64),
            state_flags jsonb NOT NULL DEFAULT '{}'::jsonb,
            standard_version varchar(20) NOT NULL,
            correlation_id uuid NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            last_failure_class varchar(40),
            attention_reason varchar(1000),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );
        CREATE INDEX ix_evaluation_run_tenant_updated ON evaluation_run (tenant_id, updated_at);

        CREATE TABLE run_manifest (
            run_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            manifest_sha256 varchar(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-fA-F]{64}$'),
            frozen_config jsonb NOT NULL DEFAULT '{}'::jsonb,
            budget jsonb NOT NULL DEFAULT '{}'::jsonb,
            security_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, run_id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );

        CREATE TABLE stage (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            code varchar(64) NOT NULL,
            ordinal integer NOT NULL CHECK (ordinal > 0),
            status varchar(32) NOT NULL,
            started_at timestamptz,
            completed_at timestamptz,
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id, code),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE INDEX ix_stage_tenant_run_ordinal ON stage (tenant_id, run_id, ordinal);

        CREATE TABLE task (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            stage_id uuid NOT NULL,
            agent_identity_id uuid,
            skill_version_id uuid,
            stage_code varchar(64) NOT NULL,
            agent_identity_ref varchar(200) NOT NULL DEFAULT 'unspecified',
            skill_ref varchar(200) NOT NULL DEFAULT 'unspecified',
            skill_version varchar(20) NOT NULL DEFAULT '1.0',
            status varchar(40) NOT NULL,
            lease_token varchar(255),
            idempotency_key varchar(200) NOT NULL,
            dependencies jsonb NOT NULL DEFAULT '[]'::jsonb,
            tool_allowlist jsonb NOT NULL DEFAULT '[]'::jsonb,
            budget_slice jsonb,
            timeout_seconds integer NOT NULL CHECK (timeout_seconds > 0),
            success_condition jsonb NOT NULL DEFAULT '{}'::jsonb,
            evidence_requirement varchar(1000),
            required boolean NOT NULL DEFAULT true,
            correction_attempts integer NOT NULL DEFAULT 0 CHECK (correction_attempts >= 0),
            transient_retries integer NOT NULL DEFAULT 0 CHECK (transient_retries >= 0),
            last_failure_class varchar(40),
            last_error varchar(1000),
            side_effect_started boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, stage_id) REFERENCES stage(tenant_id, id),
            FOREIGN KEY (agent_identity_id) REFERENCES agent_identity(id),
            FOREIGN KEY (skill_version_id) REFERENCES skill_version(id)
        );
        CREATE INDEX ix_task_tenant_stage_status ON task (tenant_id, stage_id, status);

        CREATE TABLE task_dependency (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            task_id uuid NOT NULL,
            depends_on_task_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, task_id, depends_on_task_id),
            CHECK (task_id <> depends_on_task_id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, depends_on_task_id) REFERENCES task(tenant_id, id)
        );

        CREATE TABLE run_status_history (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            from_status varchar(40) NOT NULL,
            to_status varchar(40) NOT NULL,
            reason varchar(1000) NOT NULL,
            failure_class varchar(40),
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE INDEX ix_run_status_history_tenant_run_time ON run_status_history (tenant_id, run_id, occurred_at);

        CREATE TABLE skill_invocation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            task_id uuid NOT NULL,
            skill_version_id uuid NOT NULL,
            status varchar(32) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            estimated_cost numeric(20,6) NOT NULL DEFAULT 0 CHECK (estimated_cost >= 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (skill_version_id) REFERENCES skill_version(id)
        );

        CREATE TABLE tool_invocation (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            skill_invocation_id uuid NOT NULL,
            tool_code varchar(120) NOT NULL,
            risk_tier varchar(32) NOT NULL,
            status varchar(32) NOT NULL,
            parameters_sha256 varchar(64) NOT NULL CHECK (parameters_sha256 ~ '^[0-9a-fA-F]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, skill_invocation_id) REFERENCES skill_invocation(tenant_id, id)
        );
        CREATE INDEX ix_tool_invocation_tenant_skill ON tool_invocation (tenant_id, skill_invocation_id, created_at);
        """
    )
    enable_tenant_rls(op, _TENANT_TABLES)
    grant_runtime(op, _TENANT_TABLES)
    op.execute("GRANT SELECT ON TABLE agent_identity, skill_version TO launchscope_runtime;")
    install_updated_at_trigger(op, "evaluation_run")
    install_updated_at_trigger(op, "task")


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
    op.execute("DROP TABLE IF EXISTS skill_version CASCADE;")
    op.execute("DROP TABLE IF EXISTS agent_identity CASCADE;")
