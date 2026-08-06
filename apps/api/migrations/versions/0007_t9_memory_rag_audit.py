"""Add T9 controlled-memory scope columns and append-only RAG retrieval log.

Existing MemoryItem rows intentionally remain without the new mandatory
retrieval metadata.  RetrievalPolicy fails closed on those legacy rows instead
of guessing a version, region, or permission boundary.
"""

from __future__ import annotations

from alembic import op

from migrations._common import enable_tenant_rls, grant_runtime, install_append_only_trigger

revision = "0007_t9_memory_rag_audit"
down_revision = "0006_t6_object_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE memory_item
            ADD COLUMN product_version_id uuid,
            ADD COLUMN source_finding_id uuid,
            ADD COLUMN region varchar(100),
            ADD COLUMN permission_scope varchar(120),
            ADD COLUMN search_text varchar(8000);
        ALTER TABLE memory_item
            ADD CONSTRAINT fk_memory_item_product_version_t9
                FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id);
        ALTER TABLE memory_item
            ADD CONSTRAINT fk_memory_item_source_finding_t9
                FOREIGN KEY (tenant_id, source_finding_id) REFERENCES finding(tenant_id, id);

        CREATE TABLE rag_retrieval (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            project_id uuid NOT NULL,
            product_version_id uuid NOT NULL,
            run_id uuid,
            query_sha256 varchar(64) NOT NULL CHECK (query_sha256 ~ '^[0-9a-fA-F]{64}$'),
            filters jsonb NOT NULL,
            hit_memory_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            hit_finding_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            hit_evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            result_sha256 varchar(64) NOT NULL CHECK (result_sha256 ~ '^[0-9a-fA-F]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (tenant_id, id),
            FOREIGN KEY (tenant_id, project_id) REFERENCES project(tenant_id, id),
            FOREIGN KEY (tenant_id, product_version_id) REFERENCES product_version(tenant_id, id),
            FOREIGN KEY (tenant_id, run_id) REFERENCES evaluation_run(tenant_id, id)
        );
        CREATE INDEX ix_rag_retrieval_tenant_project_time
            ON rag_retrieval (tenant_id, project_id, product_version_id, created_at);
        """
    )
    enable_tenant_rls(op, ("rag_retrieval",))
    grant_runtime(op, ("rag_retrieval",))
    install_append_only_trigger(op, "memory_item")
    install_append_only_trigger(op, "rag_retrieval")


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS rag_retrieval CASCADE;
        ALTER TABLE memory_item DROP CONSTRAINT IF EXISTS fk_memory_item_source_finding_t9;
        ALTER TABLE memory_item DROP CONSTRAINT IF EXISTS fk_memory_item_product_version_t9;
        ALTER TABLE memory_item
            DROP COLUMN IF EXISTS search_text,
            DROP COLUMN IF EXISTS permission_scope,
            DROP COLUMN IF EXISTS region,
            DROP COLUMN IF EXISTS source_finding_id,
            DROP COLUMN IF EXISTS product_version_id;
        """
    )
