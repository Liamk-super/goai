"""Seed the six approved P0 Skill hashes as immutable runtime references."""

from __future__ import annotations

from alembic import op

revision = "0012_t12_seed_p0_skills"
down_revision = "0011_t12_execution_evidence"
branch_labels = None
depends_on = None

_SKILLS = (
    ("browser-product-audit", "7213f2d1b2ba9c5003346fdf95f9b6f27fa15fcb80747dfad0de8535a4acf87e"),
    ("business-investment-assessment", "480e67be1d98e57f2a65c06f0ee29c7679f623a8b90351f0ae9b36b234059777"),
    ("evidence-grounding-audit", "45cbd0dcc1c5afc4fcfee862a2608ba9f305c4d0d34deac890c2e50bb6ee0d2c"),
    ("intake-gap-diagnosis", "69ec850b751242bb6f01c03f4cbe08ceffa9c0d3ac5c5661230b79c293a29811"),
    ("product-intake-normalizer", "0693e5f779790cb8e4e65910302fbe1cacc38f2fc35de227b5a1eceecf43d0f2"),
    ("version-regression-verification", "e10cfe33ca094a5c81e0264b2a01166af6014f332f53d3eb6f0c4ebc60e9ad30"),
)


def upgrade() -> None:
    for code, digest in _SKILLS:
        op.execute(
            f"""
            INSERT INTO skill_version
                (id, skill_code, version, manifest_sha256, input_schema, output_schema)
            VALUES
                (gen_random_uuid(), '{code}', '1.0', '{digest}', '{{}}'::jsonb, '{{}}'::jsonb)
            ON CONFLICT (skill_code, version) DO NOTHING;
            """
        )


def downgrade() -> None:
    for code, digest in _SKILLS:
        op.execute(
            f"DELETE FROM skill_version WHERE skill_code = '{code}' AND version = '1.0' "
            f"AND manifest_sha256 = '{digest}';"
        )
