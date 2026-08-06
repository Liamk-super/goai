"""Persist verified object metadata for T6 private evidence storage.

This is additive: existing material hashes remain immutable initiation facts;
the verified object metadata and terminal rejection reason are recorded without
ever storing material bytes in PostgreSQL.
"""

from __future__ import annotations

from alembic import op

revision = "0006_t6_object_evidence"
down_revision = "0005_t5_intake_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE material
            ADD COLUMN rejection_reason varchar(1000),
            ADD COLUMN object_metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
        ALTER TABLE material
            ADD CONSTRAINT ck_material_ingest_status_t6
            CHECK (ingest_status IN ('UPLOADING', 'QUARANTINED', 'VALIDATED', 'REJECTED')) NOT VALID;
        ALTER TABLE material VALIDATE CONSTRAINT ck_material_ingest_status_t6;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE material DROP CONSTRAINT IF EXISTS ck_material_ingest_status_t6;
        ALTER TABLE material DROP COLUMN IF EXISTS object_metadata;
        ALTER TABLE material DROP COLUMN IF EXISTS rejection_reason;
        """
    )
