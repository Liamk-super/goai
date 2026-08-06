"""Create evidence, finding, decision and report history.

Forward: adds evidence lineage and append-only conclusions.  Rollback removes
only these tables after dependent policy/audit tables have been downgraded.
Finding/Decision/Report rows are immutable facts; a correction inserts a new
row and points to the previous fact through ``supersedes_id``.
"""

from __future__ import annotations

from alembic import op

from migrations._common import (
    enable_tenant_rls,
    grant_runtime,
    install_append_only_trigger,
)

revision = "0003_evidence_decision"
down_revision = "0002_evaluation_manifest"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "evidence",
    "finding",
    "finding_evidence",
    "conflict_record",
    "evidence_audit",
    "decision",
    "decision_finding",
    "report",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid,
            material_id uuid,
            source_type varchar(64) NOT NULL,
            object_key varchar(1024) NOT NULL
                CHECK (object_key !~ '(^/|(^|/)\\.\\.(/|$))'),
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-fA-F]{64}$'),
            size_bytes bigint NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
            mime_type varchar(255) NOT NULL,
            evidence_level varchar(16) NOT NULL,
            trust_level varchar(16) NOT NULL,
            summary varchar(4000) NOT NULL DEFAULT '',
            published_at timestamptz,
            fetched_at timestamptz,
            valid_from timestamptz,
            valid_until timestamptz,
            region varchar(100),
            simulated boolean NOT NULL DEFAULT false,
            supersedes_id uuid,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, material_id) REFERENCES material(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_id) REFERENCES evidence(tenant_id, id)
        );
        CREATE INDEX ix_evidence_tenant_run_fetched ON evidence (tenant_id, run_id, fetched_at);
        CREATE INDEX ix_evidence_tenant_object ON evidence (tenant_id, object_key);

        CREATE TABLE finding (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid,
            dimension_code varchar(64) NOT NULL,
            grade varchar(40) NOT NULL,
            claim_type varchar(64) NOT NULL DEFAULT 'FINDING',
            statement varchar(10000) NOT NULL,
            is_hypothesis boolean NOT NULL DEFAULT false,
            submitted_by varchar(255) NOT NULL,
            submitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            supersedes_id uuid,
            structured_result jsonb NOT NULL DEFAULT '{}'::jsonb,
            simulated boolean NOT NULL DEFAULT false,
            hard_block boolean NOT NULL DEFAULT false,
            block_reason varchar(1000),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_id) REFERENCES finding(tenant_id, id)
        );
        CREATE INDEX ix_finding_tenant_run_dimension ON finding (tenant_id, run_id, dimension_code, submitted_at);

        CREATE TABLE finding_evidence (
            tenant_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            relation_type varchar(32) NOT NULL DEFAULT 'SUPPORTS',
            PRIMARY KEY (tenant_id, finding_id, evidence_id),
            FOREIGN KEY (tenant_id, finding_id) REFERENCES finding(tenant_id, id),
            FOREIGN KEY (tenant_id, evidence_id) REFERENCES evidence(tenant_id, id)
        );

        CREATE TABLE conflict_record (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            conflicting_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
            resolution_status varchar(32) NOT NULL DEFAULT 'OPEN',
            reason varchar(2000) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, finding_id) REFERENCES finding(tenant_id, id)
        );

        CREATE TABLE evidence_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            decision varchar(40) NOT NULL,
            auditor_id varchar(255) NOT NULL,
            reason varchar(2000) NOT NULL DEFAULT '',
            audited_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, finding_id) REFERENCES finding(tenant_id, id)
        );
        CREATE INDEX ix_evidence_audit_tenant_finding_time ON evidence_audit (tenant_id, finding_id, audited_at);

        CREATE TABLE decision (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            recommendation varchar(40) NOT NULL,
            standard_version varchar(20) NOT NULL,
            dimension_grades jsonb NOT NULL DEFAULT '{}'::jsonb,
            hard_blocks jsonb NOT NULL DEFAULT '[]'::jsonb,
            supersedes_id uuid,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_id) REFERENCES decision(tenant_id, id)
        );
        CREATE INDEX ix_decision_tenant_run_time ON decision (tenant_id, run_id, created_at);

        CREATE TABLE decision_finding (
            tenant_id uuid NOT NULL,
            decision_id uuid NOT NULL,
            finding_id uuid NOT NULL,
            role varchar(32) NOT NULL DEFAULT 'INFORMS',
            PRIMARY KEY (tenant_id, decision_id, finding_id),
            FOREIGN KEY (tenant_id, decision_id) REFERENCES decision(tenant_id, id),
            FOREIGN KEY (tenant_id, finding_id) REFERENCES finding(tenant_id, id)
        );

        CREATE TABLE report (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            decision_id uuid NOT NULL,
            object_key varchar(1024) NOT NULL
                CHECK (object_key !~ '(^/|(^|/)\\.\\.(/|$))'),
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-fA-F]{64}$'),
            status varchar(32) NOT NULL DEFAULT 'RENDERED',
            action_items jsonb NOT NULL DEFAULT '[]'::jsonb,
            supersedes_id uuid,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, decision_id) REFERENCES decision(tenant_id, id),
            FOREIGN KEY (tenant_id, supersedes_id) REFERENCES report(tenant_id, id)
        );
        CREATE INDEX ix_report_tenant_run_time ON report (tenant_id, run_id, created_at);
        """
    )
    enable_tenant_rls(op, _TENANT_TABLES)
    grant_runtime(op, _TENANT_TABLES)
    for table in ("evidence", "finding", "evidence_audit", "decision", "report"):
        install_append_only_trigger(op, table)


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
