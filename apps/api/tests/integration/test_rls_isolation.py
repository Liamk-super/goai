"""Cross-tenant reads, writes and composite foreign-key rejection."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from launchscope_api.infrastructure.db.schema import evidence, memory_item, project, trace_metadata
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction

from .conftest import seed_tenant


def test_tenant_a_cannot_read_or_write_tenant_b(database, runtime_engine, tenant_records) -> None:
    other = seed_tenant(database)
    scope = tenant_records["scope"]
    assert scope is not None
    factory = session_factory(runtime_engine)
    with tenant_transaction(factory, scope) as session:
        assert session.execute(select(project.c.id).where(project.c.id == other["project_id"])).first() is None
        assert session.execute(select(evidence.c.id).where(evidence.c.tenant_id == other["tenant_id"])).first() is None
        assert session.execute(
            select(memory_item.c.id).where(memory_item.c.tenant_id == other["tenant_id"])
        ).first() is None
        assert session.execute(
            select(trace_metadata.c.id).where(trace_metadata.c.tenant_id == other["tenant_id"])
        ).first() is None

    try:
        with tenant_transaction(factory, scope) as session:
            session.execute(
                project.insert().values(
                    id=other["project_id"],
                    tenant_id=other["tenant_id"],
                    workspace_id=other["workspace_id"],
                    name="cross-tenant write",
                    dossier_status="ACTIVE",
                )
                )
    except DBAPIError:
        pass
    else:
        raise AssertionError("RLS allowed a cross-tenant project write")
    try:
        with tenant_transaction(factory, scope) as session:
            session.execute(
                text(
                    "INSERT INTO project (id, tenant_id, workspace_id, name) "
                    "VALUES (:id, :tenant_id, :workspace_id, 'wrong parent tenant')"
                ),
                {
                    "id": uuid4(),
                    "tenant_id": scope.tenant_id,
                    "workspace_id": other["workspace_id"],
                },
            )
    except DBAPIError:
        pass
    else:
        raise AssertionError("composite tenant foreign key allowed a cross-tenant parent")
