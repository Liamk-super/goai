"""ADR 0007: executable user-validation Skill, immutable checkpoints, and rechecks."""

from __future__ import annotations

from alembic import op

from migrations._common import (
    enable_tenant_rls,
    grant_runtime,
    install_append_only_trigger,
    install_updated_at_trigger,
)

revision = "0017_adr0007_user_validation"
down_revision = "0016_adr0006_runtime_accounting"
branch_labels = None
depends_on = None

_UVD_MANIFEST_SHA256 = "1a206a37958f1788abfd0605746816bffc8717993411f4cb663648ed843b5b2b"
_AUDIT_MANIFEST_SHA256 = "cd0870762fde65a806918afcbc20e09a9fa8deebb74f4323bb2311f8b80abd3e"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evaluation_run
            ADD COLUMN run_kind varchar(40) NOT NULL DEFAULT 'FULL_EVALUATION'
                CHECK (run_kind IN ('FULL_EVALUATION', 'USER_EVIDENCE_RECHECK')),
            ADD COLUMN baseline_run_id uuid,
            ADD CONSTRAINT fk_evaluation_run_baseline
                FOREIGN KEY (tenant_id, baseline_run_id) REFERENCES evaluation_run(tenant_id, id),
            ADD CONSTRAINT evaluation_run_recheck_baseline
                CHECK ((run_kind = 'USER_EVIDENCE_RECHECK') = (baseline_run_id IS NOT NULL));

        ALTER TABLE evidence_audit
            ADD COLUMN contract_version varchar(20) NOT NULL DEFAULT '1.0',
            ADD COLUMN rule_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN referenced_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN score_components jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN flags jsonb NOT NULL DEFAULT '[]'::jsonb;

        CREATE TABLE user_validation_script (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            revision integer NOT NULL CHECK (revision > 0),
            object_key varchar(1024) NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            product_tasks_sha256 varchar(64) NOT NULL CHECK (product_tasks_sha256 ~ '^[0-9a-f]{64}$'),
            task_count integer NOT NULL CHECK (task_count BETWEEN 1 AND 5),
            confirmed_by varchar(255) NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            confirmed_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, product_version_id, revision),
            UNIQUE (tenant_id, product_version_id, idempotency_key),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );

        CREATE TABLE user_evidence_metadata (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            object_key varchar(1024) NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            kind varchar(40) NOT NULL CHECK (kind IN (
                'interview', 'survey', 'usability_test', 'review', 'public_comment',
                'usage_data', 'retention_data', 'payment_record', 'contract', 'team_statement'
            )),
            claimed_tier varchar(16) NOT NULL CHECK (claimed_tier IN ('E0','E1','E2','E3','E4','E5')),
            source_tier varchar(32) CHECK (source_tier IN ('tier_1','tier_2','tier_3','untraceable')),
            source varchar(1000) NOT NULL,
            observed_at timestamptz NOT NULL,
            expires_at timestamptz,
            sample_size integer CHECK (sample_size > 0),
            segment varchar(500),
            aggregate_observation varchar(4000) NOT NULL,
            applicability jsonb NOT NULL,
            supporting_claim_refs jsonb NOT NULL,
            contradicting_claim_refs jsonb NOT NULL,
            idempotency_key varchar(200) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            created_by varchar(255) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, product_version_id, idempotency_key),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id)
        );

        CREATE TABLE skill_execution (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            skill_code varchar(120) NOT NULL,
            skill_version varchar(20) NOT NULL,
            mode varchar(40) NOT NULL CHECK (mode IN ('first_validation','version_regression','evidence_recheck')),
            status varchar(40) NOT NULL
                CHECK (status IN ('AWAITING_STEP','COMPLETED','BLOCKED','FAILED','NEEDS_ATTENTION')),
            current_step varchar(16),
            revision integer NOT NULL CHECK (revision >= 0),
            checkpoint_object_key varchar(1024) NOT NULL,
            checkpoint_sha256 varchar(64) NOT NULL CHECK (checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
            idempotency_key varchar(200) NOT NULL,
            request_sha256 varchar(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
            last_error_code varchar(80),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, task_id),
            UNIQUE (tenant_id, run_id, idempotency_key),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id, task_id) REFERENCES task(tenant_id, run_id, id)
        );

        CREATE TABLE skill_execution_step (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            execution_id uuid NOT NULL,
            step_id varchar(16) NOT NULL CHECK (step_id IN ('s2','s3','s4a','s4b','s5','s6')),
            attempt integer NOT NULL CHECK (attempt BETWEEN 0 AND 2),
            revision integer NOT NULL CHECK (revision > 0),
            idempotency_key varchar(200) NOT NULL,
            input_sha256 varchar(64) NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
            output_object_key varchar(1024) NOT NULL,
            output_sha256 varchar(64) NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
            status varchar(40) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, execution_id, revision),
            UNIQUE (tenant_id, execution_id, step_id, attempt),
            UNIQUE (tenant_id, execution_id, idempotency_key),
            FOREIGN KEY (tenant_id, execution_id) REFERENCES skill_execution(tenant_id, id)
        );

        CREATE TABLE skill_result (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            execution_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            schema_version varchar(20) NOT NULL,
            mode varchar(40) NOT NULL CHECK (mode IN ('first_validation','version_regression','evidence_recheck')),
            status varchar(40) NOT NULL CHECK (status IN ('COMPLETED','PARTIAL','BLOCKED','FAILED')),
            object_key varchar(1024) NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            size_bytes integer NOT NULL CHECK (size_bytes >= 0),
            summary jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, execution_id),
            FOREIGN KEY (tenant_id, execution_id) REFERENCES skill_execution(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id, task_id) REFERENCES task(tenant_id, run_id, id)
        );

        CREATE TABLE skill_result_evidence (
            tenant_id uuid NOT NULL,
            skill_result_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            external_evidence_id varchar(255) NOT NULL,
            origin varchar(40) NOT NULL CHECK (origin IN ('skill_issued','caller_supplied')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id, skill_result_id, evidence_id),
            UNIQUE (tenant_id, skill_result_id, external_evidence_id),
            FOREIGN KEY (tenant_id, skill_result_id) REFERENCES skill_result(tenant_id, id),
            FOREIGN KEY (tenant_id, evidence_id) REFERENCES evidence(tenant_id, id)
        );

        INSERT INTO skill_version
            (id, skill_code, version, manifest_sha256, input_schema, output_schema)
        VALUES
            (gen_random_uuid(), 'user-validation-designer', '1.0.4',
             '1a206a37958f1788abfd0605746816bffc8717993411f4cb663648ed843b5b2b',
             '{"$ref":"launchscope://skills/user-validation-designer/0.1/input","sha256":"676b1d0968b1337bae5aa60dd148a17b94f885b874eddbb68cc1ea3ab816ce05"}'::jsonb,
             '{"$ref":"launchscope://skills/user-validation-designer/0.1/output","sha256":"f63f51683c06339d8696fc3c5330e34a6820d4e15d5541677e79233a8cce78a0"}'::jsonb)
        ON CONFLICT (skill_code, version) DO NOTHING;

        INSERT INTO skill_version
            (id, skill_code, version, manifest_sha256, input_schema, output_schema)
        VALUES
            (gen_random_uuid(), 'evidence-grounding-audit', '2.0',
             'cd0870762fde65a806918afcbc20e09a9fa8deebb74f4323bb2311f8b80abd3e',
             '{"$ref":"https://launchscope.local/contracts/audit/audit-request.v2.json","sha256":"d1160a64b52e30d9f02fcd2f849483faa4afb02b9e21d8d5806608e329dcb502"}'::jsonb,
             '{"$ref":"https://launchscope.local/contracts/audit/audit-result.v2.json","sha256":"b0a060fcf9061057935f345aac870e7939d688a9c384bb492a6e6c7c4c65e8cb"}'::jsonb)
        ON CONFLICT (skill_code, version) DO NOTHING;
        """
    )
    tables = (
        "user_validation_script",
        "user_evidence_metadata",
        "skill_execution",
        "skill_execution_step",
        "skill_result",
        "skill_result_evidence",
    )
    enable_tenant_rls(op, tables)
    grant_runtime(op, tables)
    install_updated_at_trigger(op, "skill_execution")
    for table in (
        "user_validation_script",
        "user_evidence_metadata",
        "skill_execution_step",
        "skill_result",
        "skill_result_evidence",
    ):
        install_append_only_trigger(op, table)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS skill_result_evidence CASCADE;")
    op.execute("DROP TABLE IF EXISTS skill_result CASCADE;")
    op.execute("DROP TABLE IF EXISTS skill_execution_step CASCADE;")
    op.execute("DROP TABLE IF EXISTS skill_execution CASCADE;")
    op.execute("DROP TABLE IF EXISTS user_evidence_metadata CASCADE;")
    op.execute("DROP TABLE IF EXISTS user_validation_script CASCADE;")
    op.execute(
        f"DELETE FROM skill_version WHERE skill_code = 'user-validation-designer' "
        f"AND version = '1.0.4' AND manifest_sha256 = '{_UVD_MANIFEST_SHA256}';"
    )
    op.execute(
        f"DELETE FROM skill_version WHERE skill_code = 'evidence-grounding-audit' "
        f"AND version = '2.0' AND manifest_sha256 = '{_AUDIT_MANIFEST_SHA256}';"
    )
    op.execute("ALTER TABLE evaluation_run DROP CONSTRAINT IF EXISTS evaluation_run_recheck_baseline;")
    op.execute("ALTER TABLE evaluation_run DROP CONSTRAINT IF EXISTS fk_evaluation_run_baseline;")
    op.execute("ALTER TABLE evaluation_run DROP COLUMN IF EXISTS baseline_run_id;")
    op.execute("ALTER TABLE evaluation_run DROP COLUMN IF EXISTS run_kind;")
    op.execute("ALTER TABLE evidence_audit DROP COLUMN IF EXISTS flags;")
    op.execute("ALTER TABLE evidence_audit DROP COLUMN IF EXISTS score_components;")
    op.execute("ALTER TABLE evidence_audit DROP COLUMN IF EXISTS referenced_evidence_ids;")
    op.execute("ALTER TABLE evidence_audit DROP COLUMN IF EXISTS rule_ids;")
    op.execute("ALTER TABLE evidence_audit DROP COLUMN IF EXISTS contract_version;")
