from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from launchscope_api.infrastructure.db.schema import (
    decision,
    evaluation_run,
    product_profile,
    product_version,
    report,
    requirement_brief,
    requirement_change,
    run_manifest,
    stage,
    supervisor_chat_message,
    task,
    user_validation_script,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evaluation.intake_application import IntakeValidationError
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.persistent_application import (
    PersistentIdentityTenantApplication,
    PersistentProjectDossierApplication,
)
from launchscope_api.modules.supervisor.intake_application import SupervisorChatApplication


class _Objects:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put_private(self, key: str, body: bytes, _content_type: str) -> str:
        self.values[key] = body
        return hashlib.sha256(body).hexdigest()

    def get_private(self, key: str, *, max_bytes: int = 2_000_000) -> bytes:
        body = self.values[key]
        if len(body) > max_bytes:
            raise ValueError("object exceeds test read limit")
        return body


def _proposal() -> dict[str, object]:
    return {
        "normalized_goal": "判断是否值得继续投入",
        "evaluation_mode": "FULL_POTENTIAL",
        "requested_deliverables": ["完整评审报告"],
        "constraints": ["不调用付费服务"],
        "success_criteria": ["给出可追溯建议"],
        "explicit_facts": {
            "target_user": "香港大学生创业团队",
            "region": "香港",
            "validation_goal": "判断是否值得继续投入",
        },
        "assumptions": [],
        "unknowns": ["payer"],
        "confidence_overall": 0.95,
        "confidence_fields": {"target_user": 0.95, "region": 0.95, "validation_goal": 0.95},
        "change_classification": "INITIAL",
        "scope_changed": False,
        "cost_changed": False,
        "permission_changed": False,
    }


def test_clear_requirement_persists_raw_and_normalized_versions_and_replays_idempotently(
    runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    objects = _Objects()
    application = SupervisorChatApplication(session_factory(runtime_engine), objects)  # type: ignore[arg-type]
    tenant_id = tenant_records["tenant_id"]
    project_id = tenant_records["project_id"]
    version_id = tenant_records["version_id"]
    actor = Actor(tenant_id, "integration-supervisor")
    message = "请评估面向香港大学生创业团队的产品，范围是香港，判断是否值得继续投入；不调用付费服务。"
    correlation_id = uuid4()
    first = application.submit_requirement(
        actor,
        project_id,
        version_id,
        message=message,
        model_output=_proposal(),
        idempotency_key="m2-clear-requirement",
        correlation_id=correlation_id,
    )
    replay = application.submit_requirement(
        actor,
        project_id,
        version_id,
        message=message,
        model_output=_proposal(),
        idempotency_key="m2-clear-requirement",
        correlation_id=correlation_id,
    )
    assert first.interaction_state == "LEADER_PLANNING"
    assert first.confirmation_required is False
    assert replay.duplicate is True and replay.brief_id == first.brief_id
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        brief = session.execute(
            select(requirement_brief).where(
                requirement_brief.c.tenant_id == tenant_id,
                requirement_brief.c.id == first.brief_id,
            )
        ).mappings().one()
        messages = session.execute(
            select(supervisor_chat_message).where(
                supervisor_chat_message.c.tenant_id == tenant_id,
                supervisor_chat_message.c.brief_id == first.brief_id,
            )
        ).mappings().all()
    assert brief["status"] == "READY_FOR_PLANNING"
    assert brief["document"]["raw_input_ref"]["sha256"] == brief["raw_input_sha256"]
    assert len(messages) == 1
    assert objects.values[brief["raw_input_object_key"]].decode() == message


def test_confirmed_profile_dispatch_builds_one_brief_and_replays_without_duplicate_runtime(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "automatic-brief-integration-secret")
    objects = _Objects()
    now = datetime.now(UTC)
    profile_id, script_id = uuid4(), uuid4()
    tasks = [
        {
            "task_key": "complete_core_flow",
            "description": "Complete the core product flow",
            "expected_observable_outcome": "The completion state is durably visible",
            "max_steps": 8,
        }
    ]
    script_document = {"schema_version": "1.0", "product_tasks_hash": "b" * 64, "tasks": tasks}
    script_body = json.dumps(
        script_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    script_sha = hashlib.sha256(script_body).hexdigest()
    script_key = f"test/{script_sha}.json"
    objects.values[script_key] = script_body
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(status="PLANNED", state_flags={"architecture_generation": "supervisor-1p4-v1"})
        )
        connection.execute(
            product_profile.insert().values(
                id=profile_id,
                tenant_id=tenant_records["tenant_id"],
                product_version_id=tenant_records["version_id"],
                confirmed_fields={
                    "one_line_value_claim": "Faster evidence-based decisions",
                    "target_user": "Product teams",
                    "payer": "Innovation leaders",
                    "region": "Hong Kong",
                    "stage": "Demo",
                    "validation_goal": "Decide whether to continue investing",
                },
                confirmation_status="CONFIRMED",
                confirmed_by="integration",
                confirmed_at=now,
                supersedes_id=None,
                created_at=now,
            )
        )
        connection.execute(
            user_validation_script.insert().values(
                id=script_id,
                tenant_id=tenant_records["tenant_id"],
                product_version_id=tenant_records["version_id"],
                revision=1,
                object_key=script_key,
                sha256=script_sha,
                product_tasks_sha256="b" * 64,
                task_count=1,
                confirmed_by="integration",
                idempotency_key="automatic-brief-script",
                request_sha256="c" * 64,
                confirmed_at=now,
                created_at=now,
            )
        )
    application = DispatchApplication(session_factory(runtime_engine), objects)  # type: ignore[arg-type]
    actor = Actor(tenant_records["tenant_id"], "integration-automatic-brief")

    first = application.dispatch(actor, tenant_records["run_id"], idempotency_key="automatic-dispatch")
    replay = application.dispatch(actor, tenant_records["run_id"], idempotency_key="automatic-dispatch")

    assert first == replay and first.status == "RUNNING" and first.task_count == 1
    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        brief = session.execute(select(requirement_brief)).mappings().one()
        brief_count = session.execute(select(func.count()).select_from(requirement_brief)).scalar_one()
        task_count = session.execute(select(func.count()).select_from(task)).scalar_one()
        manifest_count = session.execute(select(func.count()).select_from(run_manifest)).scalar_one()
        run_flags = session.execute(
            select(evaluation_run.c.state_flags).where(evaluation_run.c.id == tenant_records["run_id"])
        ).scalar_one()
    assert brief_count == task_count == manifest_count == 1
    assert brief["document"]["normalized_goal"] == "Decide whether to continue investing"
    assert brief["document"]["success_criteria"] == ["The completion state is durably visible"]
    assert brief["document"]["validation_tasks"] == tasks
    assert brief["document"]["assumptions"] == []
    assert set(brief["document"]["confidence"]["fields"].values()) == {1.0}
    assert run_flags["architecture_generation"] == "supervisor-1p4-v1"


def test_report_v2_plan_binds_latest_completed_report_once(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_REPORT_V2_ENABLED", "true")
    monkeypatch.setenv("LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED", "false")
    now = datetime.now(UTC)
    actor = Actor(tenant_records["tenant_id"], "integration-report-v2")
    prior_decision_id, prior_report_id, candidate_version_id = uuid4(), uuid4(), uuid4()
    fields = {
        "one_line_value_claim": "Faster evidence-based decisions",
        "target_user": "Product teams",
        "payer": "Innovation leaders",
        "region": "Hong Kong",
        "stage": "MVP",
        "validation_goal": "Decide whether to continue investing",
    }
    with database.begin() as connection:
        connection.execute(
            workspace_member.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                workspace_id=tenant_records["workspace_id"],
                actor_id=actor.actor_id,
                role="OWNER",
                created_at=now,
            )
        )
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(
                status="COMPLETED",
                standard_version="2.2",
                input_snapshot_sha256="a" * 64,
                content_fingerprint_sha256="b" * 64,
                report_profile_ref="supervisor-report@2.0",
                updated_at=now,
            )
        )
        connection.execute(
            decision.insert().values(
                id=prior_decision_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                recommendation="VALIDATE_FURTHER",
                standard_version="2.2",
                dimension_grades={},
                hard_blocks=[],
                supersedes_id=None,
                created_at=now,
            )
        )
        connection.execute(
            report.insert().values(
                id=prior_report_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                decision_id=prior_decision_id,
                object_key=f"test/{prior_report_id}.json",
                sha256="c" * 64,
                status="RENDERED",
                action_items=[],
                supersedes_id=None,
                created_at=now,
            )
        )
        connection.execute(
            product_version.insert().values(
                id=candidate_version_id,
                tenant_id=tenant_records["tenant_id"],
                project_id=tenant_records["project_id"],
                version_number=2,
                label="V2",
                created_at=now,
            )
        )
        connection.execute(
            product_profile.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                product_version_id=candidate_version_id,
                confirmed_fields={**fields, "region": "Singapore"},
                confirmation_status="CONFIRMED",
                confirmed_by=actor.actor_id,
                confirmed_at=now,
                supersedes_id=None,
                created_at=now,
            )
        )
    sessions = session_factory(runtime_engine)
    identity = PersistentIdentityTenantApplication(sessions)
    application = PersistentProjectDossierApplication(sessions, identity, _Objects())  # type: ignore[arg-type]

    planned = application.plan(actor, candidate_version_id, uuid4())

    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        candidate = session.execute(
            select(evaluation_run).where(evaluation_run.c.id == planned.run_id)
        ).mappings().one()
    assert candidate["baseline_run_id"] == tenant_records["run_id"]
    assert len(candidate["input_snapshot_sha256"]) == len(candidate["content_fingerprint_sha256"]) == 64
    assert candidate["report_profile_ref"] == "supervisor-report@2.0"
    assert candidate["standard_version"] == "2.2"
    assert candidate["state_flags"]["report_comparison_status"] == "COMPARABLE"


def test_automatic_brief_missing_confirmed_inputs_creates_no_runtime(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(status="PLANNED", state_flags={"architecture_generation": "supervisor-1p4-v1"})
        )
    application = DispatchApplication(session_factory(runtime_engine), _Objects())  # type: ignore[arg-type]

    with pytest.raises(IntakeValidationError, match="Product Profile has not been confirmed"):
        application.dispatch(
            Actor(tenant_records["tenant_id"], "integration-missing-profile"),
            tenant_records["run_id"],
            idempotency_key="automatic-dispatch-missing-profile",
        )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        assert session.execute(
            select(func.count()).select_from(requirement_brief).where(
                requirement_brief.c.tenant_id == tenant_records["tenant_id"]
            )
        ).scalar_one() == 0
        assert session.execute(
            select(func.count()).select_from(run_manifest).where(
                run_manifest.c.tenant_id == tenant_records["tenant_id"]
            )
        ).scalar_one() == 0
        assert session.execute(
            select(func.count()).select_from(task).where(task.c.tenant_id == tenant_records["tenant_id"])
        ).scalar_one() == 0


def _seed_v4_runtime(runtime_engine, tenant_records) -> object:
    now = datetime.now(UTC)
    stage_id, task_id = uuid4(), uuid4()
    sessions = session_factory(runtime_engine)
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == tenant_records["run_id"])
            .values(status="RUNNING", current_stage="DOMAIN_REVIEW", state_flags={}, updated_at=now)
        )
        session.execute(
            run_manifest.insert().values(
                run_id=tenant_records["run_id"],
                tenant_id=tenant_records["tenant_id"],
                frozen_config={"architecture_generation": "supervisor-1p4-v1"},
                manifest_sha256="a" * 64,
                budget={"currency": "USD", "limit": "20"},
                security_policy={},
                created_at=now,
            )
        )
        session.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                code="DOMAIN_REVIEW",
                ordinal=2,
                status="RUNNING",
                started_at=now,
                completed_at=None,
            )
        )
        session.execute(
            task.insert().values(
                id=task_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                stage_id=stage_id,
                agent_identity_id=None,
                skill_version_id=None,
                stage_code="DOMAIN_REVIEW",
                agent_identity_ref="business-investment@4.0",
                skill_ref="business-investment-assessment",
                skill_version="4.0",
                status="PENDING",
                lease_token=None,
                idempotency_key=f"runtime-change-{task_id}",
                dependencies=[],
                tool_allowlist=[],
                budget_slice={},
                timeout_seconds=600,
                success_condition=[],
                evidence_requirement="traceable",
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
    return task_id


def test_runtime_supplement_is_persisted_for_only_unstarted_v4_tasks(
    runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    task_id = _seed_v4_runtime(runtime_engine, tenant_records)
    application = SupervisorChatApplication(session_factory(runtime_engine), _Objects())  # type: ignore[arg-type]
    proposal = _proposal()
    proposal["change_classification"] = "SUPPLEMENT"
    message = "补充香港大学生创业团队的访谈事实，判断是否值得继续投入；范围仍是香港。"

    application.submit_requirement(
        Actor(tenant_records["tenant_id"], "runtime-supervisor"),
        tenant_records["project_id"],
        tenant_records["version_id"],
        message=message,
        model_output=proposal,
        idempotency_key="runtime-supplement",
        correlation_id=uuid4(),
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        change = session.execute(select(requirement_change)).mappings().one()
        run_status = session.execute(
            select(evaluation_run.c.status).where(evaluation_run.c.id == tenant_records["run_id"])
        ).scalar_one()
    assert change["status"] == "APPLIED"
    assert change["document"]["classification"] == "SUPPLEMENT"
    assert change["document"]["affected_task_ids"] == [str(task_id)]
    assert run_status == "RUNNING"


def test_material_runtime_change_waits_for_confirmation_without_mutating_started_work(
    runtime_engine, tenant_records, monkeypatch
) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    _seed_v4_runtime(runtime_engine, tenant_records)
    application = SupervisorChatApplication(session_factory(runtime_engine), _Objects())  # type: ignore[arg-type]
    proposal = _proposal()
    proposal.update({"change_classification": "REQUIREMENT_CHANGE", "scope_changed": True})
    message = "把香港范围扩大，但仍要判断是否值得继续投入，目标用户仍是香港大学生创业团队。"

    application.submit_requirement(
        Actor(tenant_records["tenant_id"], "runtime-supervisor"),
        tenant_records["project_id"],
        tenant_records["version_id"],
        message=message,
        model_output=proposal,
        idempotency_key="runtime-material-change",
        correlation_id=uuid4(),
    )

    with tenant_transaction(session_factory(runtime_engine), tenant_records["scope"]) as session:
        change = session.execute(select(requirement_change)).mappings().one()
        run = session.execute(
            select(evaluation_run.c.status, evaluation_run.c.state_flags).where(
                evaluation_run.c.id == tenant_records["run_id"]
            )
        ).mappings().one()
    assert change["status"] == "PROPOSED"
    assert change["document"]["confirmation_required"] is True
    assert run["status"] == "WAITING_FOR_APPROVAL"
    assert run["state_flags"]["waiting_for_approval"] is True
