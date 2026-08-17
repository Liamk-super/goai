from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    agent_task_ticket,
    evaluation_run,
    requirement_brief,
    run_manifest,
    stage,
    task,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.supervisor.planning_application import (
    ManagerPlanningApplication,
    ManagerPlanValidationError,
)


def _task(agent: str) -> dict[str, object]:
    return {
        "task_key": agent,
        "target_agent": agent,
        "input_refs": ["requirement-brief:current"],
        "analysis_dimensions": [agent],
        "region_scope": ["Hong Kong"],
        "as_of": date.today().isoformat(),
        "tool_policy": ["launchscope-context.get.v1"],
        "success_conditions": ["produce traceable findings and a SHA-bound report"],
        "required": True,
        "dependencies": [],
        "budget_suggestion": 2,
        "deadline_seconds": 600,
    }


def _plan(run_id, brief_id) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "plan_id": str(uuid4()),
        "run_id": str(run_id),
        "brief_id": str(brief_id),
        "plan_version": 1,
        "supersedes_plan_id": None,
        "evaluation_mode": "FULL_POTENTIAL",
        "score_profile_ref": "score-profile:full-potential@1.0",
        "tasks": [_task("user-evidence"), _task("product-engineering"), _task("business-investment")],
        "trimmed_domains": [],
        "budget_suggestion": 6,
        "deadline_suggestion_seconds": 600,
        "completion_policy": "REQUIRE_ALL",
        "replan_reason": None,
    }


def _seed_planning_inputs(database, records):
    now = datetime.now(UTC)
    brief_id = uuid4()
    stage_id = uuid4()
    planning_task_id = uuid4()
    raw_sha = "a" * 64
    brief_document = {
        "schema_version": "1.0",
        "brief_id": str(brief_id),
        "evaluation_mode": "FULL_POTENTIAL",
    }
    manifest = ManagerPlanningApplication._run_manifest("FULL_POTENTIAL")
    manifest_sha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == records["run_id"])
            .values(status="RUNNING", current_stage="LEADER_PLANNING")
        )
        connection.execute(
            run_manifest.insert().values(
                run_id=records["run_id"],
                tenant_id=records["tenant_id"],
                frozen_config=manifest,
                manifest_sha256=manifest_sha,
                budget=manifest["budget"],
                security_policy={"read_only_tools": True, "external_actions": "DENY"},
                created_at=now,
            )
        )
        connection.execute(
            requirement_brief.insert().values(
                id=brief_id,
                tenant_id=records["tenant_id"],
                product_version_id=records["version_id"],
                revision=1,
                schema_version="1.0",
                raw_input_object_key=f"test/{raw_sha}.txt",
                raw_input_sha256=raw_sha,
                document=brief_document,
                confirmation_required=False,
                status="READY_FOR_PLANNING",
                created_by="integration",
                created_at=now,
                confirmed_at=now,
            )
        )
        connection.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=records["tenant_id"],
                run_id=records["run_id"],
                code="LEADER_PLANNING",
                ordinal=1,
                status="COMPLETED",
                started_at=now,
                completed_at=now,
            )
        )
        connection.execute(
            task.insert().values(
                id=planning_task_id,
                tenant_id=records["tenant_id"],
                run_id=records["run_id"],
                stage_id=stage_id,
                agent_identity_id=None,
                skill_version_id=None,
                stage_code="LEADER_PLANNING",
                agent_identity_ref="evaluation-manager@4.0",
                skill_ref="launchscope-evaluation-manager-handoff-v1",
                skill_version="4.0",
                status="SUCCEEDED",
                lease_token=None,
                idempotency_key=f"v4-planning-{planning_task_id}",
                dependencies=[],
                tool_allowlist=["launchscope-context.get.v1"],
                budget_slice={"suggested_usd": 0},
                timeout_seconds=600,
                success_condition=["valid ManagerPlanV1"],
                evidence_requirement=None,
                required=True,
                correction_attempts=0,
                transient_retries=0,
                dispatch_epoch=0,
                last_failure_class=None,
                last_error=None,
                side_effect_started=False,
                created_at=now,
                updated_at=now,
            )
        )
    return brief_id, planning_task_id


def test_accepted_manager_plan_materializes_exactly_three_postgresql_tasks(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    brief_id, planning_task_id = _seed_planning_inputs(database, tenant_records)
    application = ManagerPlanningApplication(session_factory(runtime_engine))
    actor = Actor(tenant_records["tenant_id"], "integration-manager-plan")
    document = _plan(tenant_records["run_id"], brief_id)
    accepted = application.accept_and_materialize(actor, tenant_records["run_id"], planning_task_id, document)
    replay = application.accept_and_materialize(actor, tenant_records["run_id"], planning_task_id, document)
    assert len(accepted.task_ids) == 3 and replay.duplicate is True
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        tasks = session.execute(
            select(task).where(
                task.c.tenant_id == tenant_records["tenant_id"],
                task.c.id.in_(accepted.task_ids),
            )
        ).mappings().all()
        tickets = session.execute(
            select(agent_task_ticket).where(
                agent_task_ticket.c.tenant_id == tenant_records["tenant_id"],
                agent_task_ticket.c.plan_id == accepted.plan_id,
            )
        ).mappings().all()
        run_status = session.execute(
            select(evaluation_run.c.status, evaluation_run.c.current_stage).where(
                evaluation_run.c.tenant_id == tenant_records["tenant_id"],
                evaluation_run.c.id == tenant_records["run_id"],
            )
        ).one()
    assert {row["agent_identity_ref"] for row in tasks} == {
        "user-evidence@4.0",
        "product-engineering@4.0",
        "business-investment@4.0",
    }
    assert all(row["dependencies"] == [] and row["status"] == "READY" for row in tasks)
    assert len(tickets) == 3 and all(row["status"] == "PREPARED" for row in tickets)
    assert all(row["public_summary"]["input_refs"] == ["requirement-brief:current"] for row in tickets)
    assert run_status == ("RUNNING", "DOMAIN_REVIEW")


def test_feature_flag_dispatch_starts_only_the_v4_manager_planning_task(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "v4-planning-integration-secret")
    now = datetime.now(UTC)
    brief_id = uuid4()
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(
                status="PLANNED",
                state_flags={"architecture_generation": "supervisor-1p4-v1"},
            )
        )
        connection.execute(
            requirement_brief.insert().values(
                id=brief_id,
                tenant_id=tenant_records["tenant_id"],
                product_version_id=tenant_records["version_id"],
                revision=1,
                schema_version="1.0",
                raw_input_object_key=f"test/{brief_id}.txt",
                raw_input_sha256="d" * 64,
                document={
                    "schema_version": "1.0",
                    "brief_id": str(brief_id),
                    "evaluation_mode": "FULL_POTENTIAL",
                },
                confirmation_required=False,
                status="READY_FOR_PLANNING",
                created_by="integration",
                created_at=now,
                confirmed_at=now,
            )
        )
    result = DispatchApplication(session_factory(runtime_engine)).dispatch(
        Actor(tenant_records["tenant_id"], "integration-v4-dispatch"),
        tenant_records["run_id"],
        idempotency_key="v4-dispatch",
    )
    assert result.task_count == 1 and result.status == "RUNNING"
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        tasks = session.execute(
            select(task).where(
                task.c.tenant_id == tenant_records["tenant_id"],
                task.c.run_id == tenant_records["run_id"],
            )
        ).mappings().all()
        manifest = session.execute(
            select(run_manifest).where(run_manifest.c.run_id == tenant_records["run_id"])
        ).mappings().one()
    assert len(tasks) == 1 and tasks[0]["agent_identity_ref"] == "evaluation-manager@4.0"
    assert manifest["frozen_config"]["physical_topology"]["worker_count"] == 5
    assert manifest["frozen_config"]["agent_contract_generation"] == "v4"


def test_replan_cannot_change_a_started_task(database, runtime_engine, tenant_records, monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    brief_id, planning_task_id = _seed_planning_inputs(database, tenant_records)
    application = ManagerPlanningApplication(session_factory(runtime_engine))
    actor = Actor(tenant_records["tenant_id"], "integration-manager-replan")
    first_document = _plan(tenant_records["run_id"], brief_id)
    first = application.accept_and_materialize(actor, tenant_records["run_id"], planning_task_id, first_document)
    with database.begin() as connection:
        started_id = connection.execute(
            select(agent_task_ticket.c.task_id).where(
                agent_task_ticket.c.tenant_id == tenant_records["tenant_id"],
                agent_task_ticket.c.plan_id == first.plan_id,
                agent_task_ticket.c.target_agent == "user-evidence",
            )
        ).scalar_one()
        connection.execute(update(task).where(task.c.id == started_id).values(status="RUNNING"))
    replan = deepcopy(first_document)
    replan.update(
        plan_id=str(uuid4()),
        plan_version=2,
        supersedes_plan_id=str(first.plan_id),
        replan_reason="new non-material detail",
    )
    replan["tasks"][0]["region_scope"] = ["Singapore"]
    with pytest.raises(ManagerPlanValidationError, match="started task"):
        application.accept_and_materialize(actor, tenant_records["run_id"], planning_task_id, replan)
    with database.connect() as connection:
        accepted_count = connection.execute(
            select(agent_plan.c.id).where(
                agent_plan.c.tenant_id == tenant_records["tenant_id"],
                agent_plan.c.run_id == tenant_records["run_id"],
                agent_plan.c.status == "ACCEPTED",
            )
        ).all()
    assert len(accepted_count) == 1


def test_replan_retains_completed_task_and_replaces_only_unstarted_tasks(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    brief_id, planning_task_id = _seed_planning_inputs(database, tenant_records)
    application = ManagerPlanningApplication(session_factory(runtime_engine))
    actor = Actor(tenant_records["tenant_id"], "integration-manager-replan-positive")
    first_document = _plan(tenant_records["run_id"], brief_id)
    first = application.accept_and_materialize(actor, tenant_records["run_id"], planning_task_id, first_document)
    with database.begin() as connection:
        completed_id = connection.execute(
            select(agent_task_ticket.c.task_id).where(
                agent_task_ticket.c.tenant_id == tenant_records["tenant_id"],
                agent_task_ticket.c.plan_id == first.plan_id,
                agent_task_ticket.c.target_agent == "user-evidence",
            )
        ).scalar_one()
        connection.execute(update(task).where(task.c.id == completed_id).values(status="SUCCEEDED"))
    replan = deepcopy(first_document)
    replan.update(
        plan_id=str(uuid4()),
        plan_version=2,
        supersedes_plan_id=str(first.plan_id),
        replan_reason="user added a non-material product detail",
    )
    replan["tasks"][1]["analysis_dimensions"] = ["product-engineering", "localization"]
    accepted = application.accept_and_materialize(
        actor, tenant_records["run_id"], planning_task_id, replan
    )
    assert accepted.plan_version == 2 and completed_id in accepted.task_ids and len(accepted.task_ids) == 3
    with database.connect() as connection:
        prior_tickets = connection.execute(
            select(agent_task_ticket.c.target_agent, agent_task_ticket.c.status).where(
                agent_task_ticket.c.plan_id == first.plan_id
            )
        ).all()
    by_agent = {row.target_agent: row.status for row in prior_tickets}
    assert by_agent["user-evidence"] == "PREPARED"
    assert by_agent["product-engineering"] == by_agent["business-investment"] == "EXPIRED"
