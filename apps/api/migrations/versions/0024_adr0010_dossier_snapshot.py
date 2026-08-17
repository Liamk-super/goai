"""ADR 0010: additive immutable project dossier snapshot for generation-v4 completion."""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0024_adr0010_dossier_snapshot"
down_revision = "0023_adr0010_audit_rounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE project_dossier_snapshot (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            run_id uuid NOT NULL,
            decision_id uuid NOT NULL,
            report_id uuid NOT NULL,
            schema_version varchar(20) NOT NULL,
            document jsonb NOT NULL,
            sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            UNIQUE (tenant_id, run_id),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id),
            FOREIGN KEY (tenant_id, decision_id) REFERENCES decision(tenant_id, id),
            FOREIGN KEY (tenant_id, report_id) REFERENCES report(tenant_id, id)
        );
        CREATE INDEX ix_project_dossier_snapshot_version
            ON project_dossier_snapshot (tenant_id, product_version_id, created_at DESC);
        """
    )
    enable_tenant_rls(op, ("project_dossier_snapshot",))
    grant_runtime(op, ("project_dossier_snapshot",))
    install_append_only_trigger(op, "project_dossier_snapshot")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS project_dossier_snapshot CASCADE;")
