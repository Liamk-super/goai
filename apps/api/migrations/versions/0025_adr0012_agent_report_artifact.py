"""ADR 0012: immutable per-Agent report catalog for generation-v4 Runs."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0025_adr0012_agent_report"
down_revision = "0024_adr0010_dossier_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_report_artifact (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            run_id uuid NOT NULL,
            task_id uuid NOT NULL,
            agent_code varchar(120) NOT NULL CHECK (
                agent_code IN ('user-evidence', 'product-engineering', 'business-investment', 'evidence-auditor')
            ),
            report_kind varchar(32) NOT NULL CHECK (report_kind IN ('DOMAIN', 'AUDIT')),
            revision integer NOT NULL CHECK (revision BETWEEN 0 AND 2),
            object_key varchar(1024) NOT NULL CHECK (object_key !~ '(^/|(^|/)\\.\\.(/|$))'),
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            mime_type varchar(255) NOT NULL,
            status varchar(32) NOT NULL CHECK (status = 'AVAILABLE'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, task_id, report_kind, revision),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, task_id) REFERENCES task(tenant_id, id)
        );
        CREATE INDEX ix_agent_report_artifact_run
            ON agent_report_artifact (tenant_id, run_id, created_at, id);
        """
    )
    enable_tenant_rls(op, ("agent_report_artifact",))
    grant_runtime(op, ("agent_report_artifact",))
    install_append_only_trigger(op, "agent_report_artifact")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_report_artifact CASCADE;")
