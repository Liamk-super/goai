from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select, text

from launchscope_api.infrastructure.db.schema import budget_reservation, outbox_message, run_manifest, stage, task
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.identity_tenant.application import Actor


def test_dispatch_freezes_twenty_dollar_manifest_graph_and_outbox(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_AUTHORIZED_CASE_URL", "https://creatrades.com")
    monkeypatch.setenv("LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS", "creatrades.com,app.creatrades.com")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    tenant_id = tenant_records["tenant_id"]
    run_id = tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(
            text("UPDATE evaluation_run SET status='PLANNED' WHERE tenant_id=:tenant_id AND id=:run_id"),
            {"tenant_id": tenant_id, "run_id": run_id},
        )
    application = DispatchApplication(session_factory(runtime_engine))
    result = application._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="dispatch-test"
    )
    assert result.status == "RUNNING" and result.task_count == 7

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        manifest = session.execute(
            select(run_manifest).where(run_manifest.c.tenant_id == tenant_id, run_manifest.c.run_id == run_id)
        ).mappings().one()
        assert manifest["budget"] == {"currency": "USD", "hard_limit": "20"}
        assert manifest["frozen_config"]["agentteams"]["version"] == "v1.2.0"
        assert manifest["frozen_config"]["research_targets"] == {
            "authorized_urls": ["https://creatrades.com"]
        }
        assert session.execute(
            select(func.count()).select_from(task).where(task.c.tenant_id == tenant_id, task.c.run_id == run_id)
        ).scalar_one() == 7
        assert session.execute(
            select(func.count()).select_from(task).where(
                task.c.tenant_id == tenant_id,
                task.c.run_id == run_id,
                task.c.skill_version_id.is_(None),
            )
        ).scalar_one() == 0
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
                outbox_message.c.event_type == "evaluation.task.ready.v1",
            )
        ).mappings().one()
        assert event["publish_status"] == "PENDING"
        envelope = event["payload"]
        assert envelope["task_id"]
        assert envelope["payload"]["agent_code"] == "evaluation-manager"
        assert envelope["payload"]["context_token"]
        assert envelope["payload"]["handoff_schema"]["type"] == "object"
        assert "material_only" in envelope["payload"]["research_policy"]
        assert envelope["payload"]["research_policy"]["authorized_urls"] == ["https://creatrades.com"]
        assert envelope["payload"]["research_policy"]["browser_calls_per_task"] == 0
        assert envelope["payload"]["research_policy"]["search_queries_per_task"] == 0


def test_enabled_user_validation_dispatch_pins_v3_and_1_0_5(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_USER_VALIDATION_ENABLED", "true")
    tenant_id = tenant_records["tenant_id"]
    run_id = tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(
            text("UPDATE evaluation_run SET status='PLANNED' WHERE tenant_id=:tenant_id AND id=:run_id"),
            {"tenant_id": tenant_id, "run_id": run_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO user_validation_script
                    (id, tenant_id, product_version_id, revision, object_key, sha256,
                     product_tasks_sha256, task_count, confirmed_by, idempotency_key,
                     request_sha256, confirmed_at, created_at)
                SELECT gen_random_uuid(), tenant_id, product_version_id, 1,
                       'tenants/' || tenant_id || '/user-validation/scripts/test.json',
                       repeat('a', 64), repeat('b', 64), 1, 'integration-test', 'uvd-script',
                       repeat('c', 64), clock_timestamp(), clock_timestamp()
                FROM evaluation_run
                WHERE tenant_id=:tenant_id AND id=:run_id
                """
            ),
            {"tenant_id": tenant_id, "run_id": run_id},
        )

    result = DispatchApplication(session_factory(runtime_engine))._dispatch_legacy_for_historical_tests_only(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="dispatch-uvd-v3"
    )
    assert result.status == "RUNNING"

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        frozen = session.execute(select(run_manifest.c.frozen_config).where(
            run_manifest.c.tenant_id == tenant_id,
            run_manifest.c.run_id == run_id,
        )).scalar_one()
        user_task = session.execute(select(task).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.skill_ref == "user-validation-designer",
        )).mappings().one()

    assert frozen["schema_version"] == "3.0"
    assert frozen["user_validation"]["skill_version"] == "1.0.5"
    assert frozen["user_validation"]["presentation_version"] == "0.4"
    assert user_task["skill_version"] == "1.0.5"
