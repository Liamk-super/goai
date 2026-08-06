from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select, text

from launchscope_api.infrastructure.db.schema import budget_reservation, outbox_message, run_manifest, stage, task
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.identity_tenant.application import Actor


def test_dispatch_freezes_twenty_dollar_manifest_graph_and_outbox(
    database, runtime_engine, tenant_records
) -> None:
    tenant_id = tenant_records["tenant_id"]
    run_id = tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(
            text("UPDATE evaluation_run SET status='PLANNED' WHERE tenant_id=:tenant_id AND id=:run_id"),
            {"tenant_id": tenant_id, "run_id": run_id},
        )
    application = DispatchApplication(session_factory(runtime_engine))
    result = application.dispatch(Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="dispatch-test")
    assert result.status == "RUNNING" and result.task_count == 7

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        manifest = session.execute(
            select(run_manifest).where(run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id)
        ).mappings().one()
        assert manifest["budget"] == {"currency": "USD", "hard_limit": "20"}
        assert manifest["frozen_config"]["agentteams"]["version"] == "v1.2.0"
        assert session.execute(
            select(func.count()).select_from(task).where(task.c.tenant_id == tenant_id, task.c.run_id == run_id)
        ).scalar_one() == 7
        assert session.execute(
            select(func.count()).select_from(stage).where(stage.c.tenant_id == tenant_id, stage.c.run_id == run_id)
        ).scalar_one() == 4
        budget = session.execute(
            select(budget_reservation.c.limit_amount).where(
                budget_reservation.c.tenant_id == tenant_id, budget_reservation.c.run_id == run_id
            )
        ).scalar_one()
        assert budget == Decimal("20")
        event = session.execute(
            select(outbox_message).where(
                outbox_message.c.tenant_id == tenant_id,
                outbox_message.c.event_type == "evaluation.run.dispatched.v1",
            )
        ).mappings().one()
        assert event["publish_status"] == "PENDING"
