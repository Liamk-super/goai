"""ADR 0008: register user-validation-designer 1.0.5 dual-report contract."""

from __future__ import annotations

from alembic import op

revision = "0018_adr0008_uvd_dual_reports"
down_revision = "0017_adr0007_user_validation"
branch_labels = None
depends_on = None

_MANIFEST_SHA256 = "0964927ad124e301386b21626ef59f2f161230c55acc534b980b2a267d3ad285"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM skill_version
                WHERE skill_code = 'user-validation-designer'
                  AND version = '1.0.5'
                  AND manifest_sha256 <> '{_MANIFEST_SHA256}'
            ) THEN
                RAISE EXCEPTION 'user-validation-designer 1.0.5 manifest hash conflict';
            END IF;

            INSERT INTO skill_version
                (id, skill_code, version, manifest_sha256, input_schema, output_schema)
            VALUES
                (gen_random_uuid(), 'user-validation-designer', '1.0.5',
                 '{_MANIFEST_SHA256}',
                 '{{"$ref":"launchscope://skills/user-validation-designer/0.1/input","sha256":"676b1d0968b1337bae5aa60dd148a17b94f885b874eddbb68cc1ea3ab816ce05"}}'::jsonb,
                 '{{"$ref":"launchscope://skills/user-validation-designer/0.2/output","sha256":"81bfb80b385bfc9e3cb9429c88aae6eeeb40251c64817eccceed291c16990fbf"}}'::jsonb)
            ON CONFLICT (skill_code, version) DO NOTHING;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM skill_version WHERE skill_code = 'user-validation-designer' "
        f"AND version = '1.0.5' AND manifest_sha256 = '{_MANIFEST_SHA256}';"
    )
