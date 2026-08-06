from __future__ import annotations

from sqlalchemy import select, text

from launchscope_api.infrastructure.db.schema import evaluation_run, outbox_message
from launchscope_api.infrastructure.db.session import create_database_engine, session_factory, tenant_transaction
from launchscope_api.infrastructure.messaging.publisher import OutboxPublisher
from launchscope_api.infrastructure.messaging.publisher_daemon import pending_tenants
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.identity_tenant.application import Actor


class _AckUnknown:
    def publish(self, topic: str, body: bytes, *, key: str, tag: str) -> str:
        raise TimeoutError("sanitized timeout")


def test_publisher_role_can_claim_and_unknown_ack_freezes_without_retry(
    database, runtime_engine, tenant_records
) -> None:
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
    runtime_sessions = session_factory(runtime_engine)
    DispatchApplication(runtime_sessions).dispatch(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="publisher-unknown"
    )
    publisher_engine = create_database_engine(
        database.url.render_as_string(hide_password=False), application_role="launchscope_publisher"
    )
    publisher_sessions = session_factory(publisher_engine)
    with publisher_sessions() as session:
        assert tenant_id in pending_tenants(session)
    result = OutboxPublisher(
        publisher_sessions, _AckUnknown(), publisher_id="publisher-test"
    ).publish_scope(tenant_records["scope"])
    assert result.failed == 1 and result.published == 0
    with tenant_transaction(runtime_sessions, tenant_records["scope"]) as session:
        assert session.execute(select(outbox_message.c.publish_status).where(
            outbox_message.c.aggregate_id == run_id
        )).scalar_one() == "SUBMISSION_UNKNOWN"
        run = session.execute(select(
            evaluation_run.c.status, evaluation_run.c.last_failure_class
        ).where(evaluation_run.c.id == run_id)).one()
        assert run == ("NEEDS_ATTENTION", "SUBMISSION_UNKNOWN")
    publisher_engine.dispose()
