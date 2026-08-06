"""Add a narrowly scoped, separately authenticated Ops read role for T10.

The role is read-only and may see only run and outbox metadata.  It has no
rights to Evidence, Finding, Decision, Report, Material, profile, or any
object-store credential.  Tenant runtime policies remain unchanged.
"""

from __future__ import annotations

from alembic import op

revision = "0008_t10_ops_read_role"
down_revision = "0007_t9_memory_rag_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'launchscope_ops') THEN
                CREATE ROLE launchscope_ops NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            ELSE
                ALTER ROLE launchscope_ops NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO launchscope_ops;
        GRANT SELECT ON TABLE evaluation_run, outbox_message TO launchscope_ops;
        DROP POLICY IF EXISTS evaluation_run_ops_read ON evaluation_run;
        CREATE POLICY evaluation_run_ops_read ON evaluation_run FOR SELECT TO launchscope_ops USING (true);
        DROP POLICY IF EXISTS outbox_message_ops_read ON outbox_message;
        CREATE POLICY outbox_message_ops_read ON outbox_message FOR SELECT TO launchscope_ops USING (true);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS outbox_message_ops_read ON outbox_message;
        DROP POLICY IF EXISTS evaluation_run_ops_read ON evaluation_run;
        REVOKE SELECT ON TABLE evaluation_run, outbox_message FROM launchscope_ops;
        DROP ROLE IF EXISTS launchscope_ops;
        """
    )
