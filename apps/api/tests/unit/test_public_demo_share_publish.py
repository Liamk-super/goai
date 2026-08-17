from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    metadata,
    public_demo_disclosure_acceptance,
    public_demo_share,
    report,
)
from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.modules.experience.public_share import PublicDemoShareApplication, PublicSharePublishError
from launchscope_api.modules.identity_tenant.application import Actor


def _fixture(*, disclosure: bool = True, run_status: str = "COMPLETED", disclosure_bound: bool = True):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    sessions = session_factory(engine)
    actor = Actor(uuid4(), "demo-owner")
    project_id, version_id, run_id, report_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    with sessions.begin() as session:
        session.execute(
            evaluation_run.insert().values(
                id=run_id,
                tenant_id=actor.tenant_id,
                project_id=project_id,
                product_version_id=version_id,
                status=run_status,
                current_stage=run_status,
                state_flags={},
                standard_version="2.0",
                correlation_id=uuid4(),
                idempotency_key=f"run:{run_id}",
                run_kind="FULL_EVALUATION",
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            report.insert().values(
                id=report_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                decision_id=uuid4(),
                object_key=f"reports/{report_id}.json",
                sha256="a" * 64,
                status="COMMITTED",
                action_items=[],
                created_at=now,
            )
        )
        if disclosure:
            session.execute(
                public_demo_disclosure_acceptance.insert().values(
                    id=uuid4(),
                    tenant_id=actor.tenant_id,
                    project_id=project_id,
                    product_version_id=version_id,
                    run_id=run_id if disclosure_bound else None,
                    actor_id=actor.actor_id,
                    policy_version="public-demo-evidence-v1",
                    accepted_at=now,
                    created_at=now,
                )
            )
    return PublicDemoShareApplication(sessions), sessions, actor, report_id


def test_publish_creates_one_replayable_full_public_share_after_disclosure() -> None:
    application, sessions, actor, report_id = _fixture()

    first = application.publish(actor, report_id, idempotency_key="share-once")
    second = application.publish(actor, report_id, idempotency_key="share-once")

    assert first == second
    assert first["token"] == first["share_id"]
    assert first["include_agent_reports"] is True
    assert first["include_evidence"] is True
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(public_demo_share)) == 1


def test_publish_binds_a_pre_run_disclosure_to_the_exact_completed_report_run() -> None:
    application, sessions, actor, report_id = _fixture(disclosure_bound=False)
    result = application.publish(actor, report_id, idempotency_key="share-once")
    with sessions() as session:
        bound_run_id = session.scalar(select(public_demo_disclosure_acceptance.c.run_id))
    assert str(bound_run_id) == result["run_id"]


def test_publish_reuses_version_disclosure_for_a_later_completed_run() -> None:
    application, sessions, actor, first_report_id = _fixture()
    with sessions.begin() as session:
        first_run_id = select(report.c.run_id).where(report.c.id == first_report_id).scalar_subquery()
        first_run = session.execute(
            select(evaluation_run).where(evaluation_run.c.id == first_run_id)
        ).mappings().one()
        second_run_id, second_report_id = uuid4(), uuid4()
        now = datetime.now(UTC)
        session.execute(
            evaluation_run.insert().values(
                id=second_run_id,
                tenant_id=actor.tenant_id,
                project_id=first_run["project_id"],
                product_version_id=first_run["product_version_id"],
                status="COMPLETED",
                current_stage="COMPLETED",
                state_flags={},
                standard_version="2.0",
                correlation_id=uuid4(),
                idempotency_key=f"run:{second_run_id}",
                run_kind="FULL_EVALUATION",
                created_at=now,
                updated_at=now,
            )
        )
        session.execute(
            report.insert().values(
                id=second_report_id,
                tenant_id=actor.tenant_id,
                run_id=second_run_id,
                decision_id=uuid4(),
                object_key=f"reports/{second_report_id}.json",
                sha256="b" * 64,
                status="COMMITTED",
                action_items=[],
                created_at=now,
            )
        )

    result = application.publish(actor, second_report_id, idempotency_key="share-later-run")

    assert result["run_id"] == str(second_run_id)
    assert result["report_id"] == str(second_report_id)


@pytest.mark.parametrize(
    ("disclosure", "run_status", "message"),
    [(False, "COMPLETED", "disclosure"), (True, "RUNNING", "completed")],
)
def test_publish_requires_disclosure_and_a_completed_run(disclosure: bool, run_status: str, message: str) -> None:
    application, _sessions, actor, report_id = _fixture(disclosure=disclosure, run_status=run_status)
    with pytest.raises(PublicSharePublishError, match=message):
        application.publish(actor, report_id, idempotency_key="share-once")
