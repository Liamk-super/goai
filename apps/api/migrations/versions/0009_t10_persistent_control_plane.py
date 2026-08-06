"""Permit the restricted runtime role to provision a new tenant root.

Tenant creation is the one identity-root command that precedes a tenant RLS
scope.  The role receives INSERT only: it cannot enumerate or read other
tenants, while all tenant-scoped rows remain protected by RLS.
"""

from __future__ import annotations

from alembic import op

revision = "0009_t10_persistent_plane"
down_revision = "0008_t10_ops_read_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT INSERT ON TABLE tenant TO launchscope_runtime;")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON TABLE tenant FROM launchscope_runtime;")
