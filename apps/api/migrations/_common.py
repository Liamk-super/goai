"""Small, audited SQL helpers shared by the T4 migrations."""

from __future__ import annotations

import re
from collections.abc import Iterable

from alembic.operations import Operations

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def _identifier(value: str) -> str:
    if _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"unsafe migration identifier: {value}")
    return value


def create_runtime_role(op: Operations) -> None:
    """Create a non-owner role used by runtime/tests to exercise RLS.

    The role is intentionally NOLOGIN.  Deployments should grant the same
    privileges to their own managed login role and may use ``SET ROLE`` in the
    connection pool.  Keeping the migration role non-login prevents a default
    password from becoming a credential.
    """

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'launchscope_runtime') THEN
                CREATE ROLE launchscope_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            ELSE
                ALTER ROLE launchscope_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
            END IF;
        END $$;
        GRANT USAGE ON SCHEMA public TO launchscope_runtime;
        """
    )


def grant_runtime(op: Operations, tables: Iterable[str]) -> None:
    for table in tables:
        name = _identifier(table)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {name} TO launchscope_runtime;")


def enable_tenant_rls(op: Operations, tables: Iterable[str]) -> None:
    for table in tables:
        name = _identifier(table)
        op.execute(
            f"""
            ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE {name} FORCE ROW LEVEL SECURITY;
            DROP POLICY IF EXISTS {name}_tenant_isolation ON {name};
            CREATE POLICY {name}_tenant_isolation ON {name}
                USING (tenant_id = launchscope_current_tenant_id())
                WITH CHECK (tenant_id = launchscope_current_tenant_id());
            """
        )


def disable_tenant_rls(op: Operations, tables: Iterable[str]) -> None:
    for table in tables:
        name = _identifier(table)
        op.execute(f"DROP POLICY IF EXISTS {name}_tenant_isolation ON {name};")
        op.execute(f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY;")


def install_updated_at_trigger(op: Operations, table: str) -> None:
    name = _identifier(table)
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS {name}_set_updated_at ON {name};
        CREATE TRIGGER {name}_set_updated_at
        BEFORE UPDATE ON {name}
        FOR EACH ROW EXECUTE FUNCTION launchscope_set_updated_at();
        """
    )


def install_append_only_trigger(op: Operations, table: str) -> None:
    name = _identifier(table)
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS {name}_append_only ON {name};
        CREATE TRIGGER {name}_append_only
        BEFORE UPDATE OR DELETE ON {name}
        FOR EACH ROW EXECUTE FUNCTION launchscope_reject_append_only();
        """
    )
