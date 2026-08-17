"""ADR 0010: additive generation-v4 audit round and remediation metadata."""

from __future__ import annotations

from alembic import op

revision = "0023_adr0010_audit_rounds"
down_revision = "0022_adr0010_requirement_brief"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE evidence_audit ALTER COLUMN reason TYPE varchar(4000);
        ALTER TABLE evidence_audit ADD COLUMN source_finding_sha256 varchar(64);
        ALTER TABLE evidence_audit ADD COLUMN audit_round integer;
        ALTER TABLE evidence_audit ADD COLUMN remediation_target jsonb;
        ALTER TABLE evidence_audit ADD CONSTRAINT evidence_audit_source_sha256_check
            CHECK (source_finding_sha256 IS NULL OR source_finding_sha256 ~ '^[0-9a-f]{64}$');
        ALTER TABLE evidence_audit ADD CONSTRAINT evidence_audit_round_check
            CHECK (audit_round IS NULL OR audit_round BETWEEN 1 AND 2);
        CREATE UNIQUE INDEX uq_evidence_audit_generation_v4_round
            ON evidence_audit (tenant_id, finding_id, audit_round)
            WHERE audit_round IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS uq_evidence_audit_generation_v4_round;
        ALTER TABLE evidence_audit DROP CONSTRAINT IF EXISTS evidence_audit_round_check;
        ALTER TABLE evidence_audit DROP CONSTRAINT IF EXISTS evidence_audit_source_sha256_check;
        ALTER TABLE evidence_audit DROP COLUMN IF EXISTS remediation_target;
        ALTER TABLE evidence_audit DROP COLUMN IF EXISTS audit_round;
        ALTER TABLE evidence_audit DROP COLUMN IF EXISTS source_finding_sha256;
        ALTER TABLE evidence_audit ALTER COLUMN reason TYPE varchar(2000);
        """
    )
