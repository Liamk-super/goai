"""Add a least-privilege cross-tenant Outbox publisher role."""

from __future__ import annotations

from alembic import op

revision = "0014_v02_publisher_role"
down_revision = "0013_v02_async_agentteams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'launchscope_publisher') THEN
                CREATE ROLE launchscope_publisher NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            ELSE
                ALTER ROLE launchscope_publisher NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO launchscope_publisher;
        GRANT SELECT, UPDATE ON TABLE outbox_message, evaluation_run TO launchscope_publisher;
        GRANT INSERT ON TABLE run_status_history TO launchscope_publisher;

        DROP POLICY IF EXISTS outbox_message_publisher ON outbox_message;
        CREATE POLICY outbox_message_publisher ON outbox_message TO launchscope_publisher
            USING (true) WITH CHECK (true);
        DROP POLICY IF EXISTS evaluation_run_publisher ON evaluation_run;
        CREATE POLICY evaluation_run_publisher ON evaluation_run TO launchscope_publisher
            USING (true) WITH CHECK (true);
        DROP POLICY IF EXISTS run_status_history_publisher ON run_status_history;
        CREATE POLICY run_status_history_publisher ON run_status_history FOR INSERT TO launchscope_publisher
            WITH CHECK (true);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS run_status_history_publisher ON run_status_history;
        DROP POLICY IF EXISTS evaluation_run_publisher ON evaluation_run;
        DROP POLICY IF EXISTS outbox_message_publisher ON outbox_message;
        REVOKE INSERT ON TABLE run_status_history FROM launchscope_publisher;
        REVOKE SELECT, UPDATE ON TABLE outbox_message, evaluation_run FROM launchscope_publisher;
        DROP ROLE IF EXISTS launchscope_publisher;
        """
    )
