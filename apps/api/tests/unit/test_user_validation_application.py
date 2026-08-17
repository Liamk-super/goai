from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    metadata,
    product_profile,
    product_version,
    project,
    run_manifest,
    skill_execution,
    skill_result,
    stage,
    task,
    user_validation_script,
    workspace,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.main import create_app
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError, NotFoundError
from launchscope_api.modules.user_validation.application import (
    ArtifactIntegrityError,
    IdempotencyConflictError,
    ReportTooLargeError,
    UserValidationApplication,
)
from launchscope_api.modules.user_validation.runner import NodeUserValidationRunner


class MemoryObjects:
    def __init__(self) -> None:
        self.values: dict[str, tuple[bytes, str, str]] = {}

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        self.values[object_key] = (payload, digest, mime_type)
        return digest

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes:
        payload = self.values[object_key][0]
        if len(payload) > max_bytes:
            raise ValueError("too large")
        return payload

    def head(self, object_key: str):
        item = self.values.get(object_key)
        if item is None:
            return None
        return SimpleNamespace(size_bytes=len(item[0]), sha256=item[1], mime_type=item[2])

    def signed_read_url(self, object_key: str) -> str:
        if object_key not in self.values:
            raise ValueError("missing")
        return f"https://objects.invalid/{object_key}"


def _application() -> tuple[UserValidationApplication, Actor, dict[str, object], MemoryObjects]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    sessions = session_factory(engine)
    objects = MemoryObjects()
    tenant_id, workspace_id, project_id, version_id, run_id, stage_id, task_id = (uuid4() for _ in range(7))
    actor = Actor(tenant_id, "owner")
    now = datetime.now(UTC)
    manifest_sha = "a" * 64
    with sessions.begin() as session:
        session.execute(workspace.insert().values(
            id=workspace_id, tenant_id=tenant_id, name="Workspace", status="ACTIVE", created_at=now,
        ))
        session.execute(workspace_member.insert().values(
            id=uuid4(), tenant_id=tenant_id, workspace_id=workspace_id, actor_id=actor.actor_id,
            role="OWNER", created_at=now,
        ))
        session.execute(project.insert().values(
            id=project_id, tenant_id=tenant_id, workspace_id=workspace_id, name="证据助手",
            dossier_status="DRAFT", created_at=now, updated_at=now,
        ))
        session.execute(product_version.insert().values(
            id=version_id, tenant_id=tenant_id, project_id=project_id, version_number=1, label="V1",
            stage="mvp", status="DRAFT", created_at=now,
        ))
        session.execute(product_profile.insert().values(
            id=uuid4(), tenant_id=tenant_id, product_version_id=version_id,
            confirmed_fields={
                "one_line_value_claim": "帮助香港独立零售店主在十分钟内发现本周库存异常",
                "target_user": "每周盘点库存的香港独立零售店主",
                "problem": "人工盘点难以及时发现缺货和积压",
                "core_features": "按周生成可解释的库存异常清单",
                "payer": "店主", "stage": "mvp", "region": "香港",
                "validation_goal": "验证店主是否会持续使用异常清单",
            },
            confirmation_status="CONFIRMED", confirmed_by=actor.actor_id, confirmed_at=now, created_at=now,
        ))
        session.execute(evaluation_run.insert().values(
            id=run_id, tenant_id=tenant_id, project_id=project_id, product_version_id=version_id,
            status="RUNNING", current_stage="DOMAIN_REVIEW", state_flags={}, standard_version="1.0",
            correlation_id=uuid4(), idempotency_key="run", run_kind="FULL_EVALUATION",
            created_at=now, updated_at=now,
        ))
        session.execute(run_manifest.insert().values(
            run_id=run_id,
            tenant_id=tenant_id,
            frozen_config={
                "agent_contract_generation": "v3",
                "user_validation": {
                    "enabled": True,
                    "mode": "first_validation",
                    "skill_version": "1.0.5",
                    "runner_sha256": "f0923fd01aa203217b85d1c6683dc7783cf1af10019f13302dadda13d37b10f0",
                    "prompt_sha256": "a46381cbe819f6e09ae7df196295989bd4b3261470474be201497debd2e341a2",
                    "knowledge_package_sha256": "d5951922224c9d16e9b013139795d074c706c3f589f8ffec918c499e910300d2",
                },
            },
            manifest_sha256=manifest_sha, budget={}, security_policy={}, created_at=now,
        ))
        session.execute(stage.insert().values(
            id=stage_id, tenant_id=tenant_id, run_id=run_id, code="DOMAIN_REVIEW", ordinal=1,
            status="READY",
        ))
        session.execute(task.insert().values(
            id=task_id, tenant_id=tenant_id, run_id=run_id, stage_id=stage_id,
            stage_code="DOMAIN_REVIEW", agent_identity_ref="user-evidence@3.0",
            skill_ref="user-validation-designer", skill_version="1.0.5", status="RUNNING",
            idempotency_key="task", dependencies=[], tool_allowlist=["user-validation-designer.start.v1"],
            timeout_seconds=1200, success_condition={"schema": "AgentHandoffV2"},
            required=True, correction_attempts=0, transient_retries=0, dispatch_epoch=0,
            side_effect_started=False, created_at=now, updated_at=now,
        ))
    return (
        UserValidationApplication(sessions, objects, NodeUserValidationRunner()),
        actor,
        {
            "version_id": version_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "task_id": task_id,
            "manifest_sha": manifest_sha,
            "sessions": sessions,
        },
        objects,
    )


def _tasks() -> list[dict[str, object]]:
    return [{
        "task_key": "review_anomalies",
        "description": "查看本周库存异常并选择一个需要处理的商品",
        "expected_observable_outcome": "页面显示异常原因并记录所选商品",
        "max_steps": 6,
    }]


def _seed_result(
    application: UserValidationApplication,
    actor: Actor,
    ids: dict[str, object],
    objects: MemoryObjects,
) -> None:
    report = json.loads(
        (
            NodeUserValidationRunner()._root
            / "packages"
            / "user-validation-designer"
            / "examples"
            / "output.example.json"
        ).read_text(encoding="utf-8")
    )
    presentation = application._presentation_metadata(report)
    artifact = {
        "schema_version": "launchscope.user-validation-result.v1",
        "launchscope": {"run_id": str(ids["run_id"])},
        "uvd_report": report,
    }
    body = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()
    object_key = f"tenants/{actor.tenant_id}/user-validation/results/{ids['run_id']}/{digest}.json"
    objects.put_private(object_key, body, "application/json")
    execution_id, result_id = uuid4(), uuid4()
    with ids["sessions"].begin() as session:
        session.execute(skill_execution.insert().values(
            id=execution_id, tenant_id=actor.tenant_id, run_id=ids["run_id"], task_id=ids["task_id"],
            skill_code="user-validation-designer", skill_version="1.0.5", mode="first_validation",
            status="COMPLETED", current_step=None, revision=7, checkpoint_object_key="checkpoint",
            checkpoint_sha256="b" * 64, idempotency_key="seed", request_sha256="c" * 64,
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ))
        session.execute(skill_result.insert().values(
            id=result_id, tenant_id=actor.tenant_id, execution_id=execution_id, run_id=ids["run_id"],
            task_id=ids["task_id"], schema_version="1.0.5", mode="first_validation", status="COMPLETED",
            object_key=object_key, sha256=digest, size_bytes=len(body),
            summary={"result_summary": report["result_summary"], "presentation": presentation},
            created_at=datetime.now(UTC),
        ))


def test_script_is_content_addressed_and_idempotent() -> None:
    application, actor, ids, _objects = _application()
    first = application.put_script(
        actor, ids["version_id"], _tasks(), idempotency_key="script-1", correlation_id="corr-1"
    )
    replay = application.put_script(
        actor, ids["version_id"], _tasks(), idempotency_key="script-1", correlation_id="corr-replay"
    )
    assert replay == first
    with pytest.raises(IdempotencyConflictError, match="IDEMPOTENCY_CONFLICT"):
        changed = _tasks()
        changed[0]["description"] = "不同任务"
        application.put_script(
            actor, ids["version_id"], changed, idempotency_key="script-1", correlation_id="corr-1"
        )


def test_user_agent_starts_real_runner_and_persists_only_checkpoint_reference() -> None:
    application, actor, ids, objects = _application()
    application.put_script(
        actor, ids["version_id"], _tasks(), idempotency_key="script-1", correlation_id="corr-1"
    )
    result = application.start(
        actor,
        ids["run_id"],
        ids["task_id"],
        expected_revision=0,
        checkpoint_sha256=ids["manifest_sha"],
        idempotency_key="start-1",
        correlation_id="corr-2",
    )
    assert result["status"] == "awaiting_step"
    assert result["step"]["step_id"] == "s2"
    assert "checkpoint" not in result
    checkpoint_key = next(key for key in objects.values if "/checkpoints/" in key)
    checkpoint = json.loads(objects.get_private(checkpoint_key))
    description = checkpoint["input"]["product_profile"]["description"]
    assert "problem: 人工盘点" in description
    assert "core_features: 按周生成" in description
    assert "region: 香港" in description
    with ids["sessions"]() as session:
        row = session.execute(user_validation_script.select()).mappings().one()
        assert objects.head(row["object_key"]).sha256 == row["sha256"]


def test_script_rejects_pii_before_object_write() -> None:
    application, actor, ids, objects = _application()
    tasks = _tasks()
    tasks[0]["description"] = "联系 test@example.com 完成访谈"
    with pytest.raises(ValueError, match="PII"):
        application.put_script(
            actor, ids["version_id"], tasks, idempotency_key="script-pii", correlation_id="corr"
        )
    assert objects.values == {}


def test_user_evidence_rejects_another_tenant_object_prefix() -> None:
    application, actor, ids, objects = _application()
    payload = b"aggregate"
    foreign_key = f"tenant/{uuid4()}/materials/evidence.json"
    digest = objects.put_private(foreign_key, payload, "application/json")
    with pytest.raises(AuthorizationError, match="tenant prefix"):
        application.register_evidence(
            actor,
            ids["version_id"],
            {
                "object_key": foreign_key,
                "sha256": digest,
                "kind": "interview",
                "claimed_tier": "E3",
                "source": "aggregate interview notes",
                "observed_at": datetime.now(UTC),
                "aggregate_observation": "8 of 10 users completed the task",
                "applicability": {},
                "supporting_claim_refs": [],
                "contradicting_claim_refs": [],
            },
            idempotency_key="evidence-1",
            correlation_id="corr",
        )


def test_user_evidence_registers_datetime_payload_and_replays_idempotently() -> None:
    application, actor, ids, objects = _application()
    object_key = f"tenants/{actor.tenant_id}/materials/production-observation.txt"
    body = b"One production image task completed through the normal web UI."
    digest = objects.put_private(object_key, body, "text/plain")
    payload = {
        "object_key": object_key,
        "sha256": digest,
        "kind": "usage_data",
        "claimed_tier": "E3",
        "source": "production UI task 2087601640822685698",
        "observed_at": datetime(2026, 8, 13, 2, 13, tzinfo=UTC),
        "expires_at": None,
        "sample_size": 1,
        "segment": "authenticated production account",
        "aggregate_observation": "One image task completed and consumed the displayed credits.",
        "applicability": {},
        "supporting_claim_refs": [],
        "contradicting_claim_refs": [],
    }

    created = application.register_evidence(
        actor,
        ids["version_id"],
        payload,
        idempotency_key="production-observation-1",
        correlation_id="corr",
    )
    replayed = application.register_evidence(
        actor,
        ids["version_id"],
        payload,
        idempotency_key="production-observation-1",
        correlation_id="corr",
    )

    assert created == replayed
    assert created["kind"] == "usage_data"
    assert created["claimed_tier"] == "E3"


def test_user_evidence_rejects_pii_inside_the_private_aggregate() -> None:
    application, actor, ids, objects = _application()
    object_key = f"tenants/{actor.tenant_id}/materials/evidence.txt"
    body = b"participant email: test@example.com"
    digest = objects.put_private(object_key, body, "text/plain")

    with pytest.raises(ValueError, match="PII"):
        application.register_evidence(
            actor,
            ids["version_id"],
            {
                "object_key": object_key,
                "sha256": digest,
                "kind": "interview",
                "claimed_tier": "E3",
                "source": "aggregate interview notes",
                "observed_at": datetime.now(UTC),
                "aggregate_observation": "8 of 10 users completed the task",
                "applicability": {},
                "supporting_claim_refs": [],
                "contradicting_claim_refs": [],
            },
            idempotency_key="evidence-pii",
            correlation_id="corr",
        )


def test_runner_adapter_rejects_tampered_checkpoint() -> None:
    runner = NodeUserValidationRunner()
    sample = json.loads(
        (runner._root / "packages" / "user-validation-designer" / "examples" / "input.example.json").read_text(
            encoding="utf-8"
        )
    )
    started = runner.invoke({"action": "start", "input": sample, "now": "2026-08-11T00:00:00.000Z"})
    started["checkpoint"]["input"]["project_id"] = "tampered"
    with pytest.raises(ValueError, match="checkpoint hash"):
        runner.invoke({
            "action": "resume",
            "checkpoint": started["checkpoint"],
            "expected_revision": started["revision"],
            "checkpoint_hash": started["checkpoint_hash"],
        })


def test_terminal_runner_status_preserves_blocked_semantics() -> None:
    assert UserValidationApplication._execution_status(
        {"status": "completed", "result": {"status": "blocked"}}
    ) == "BLOCKED"


def test_dual_report_reads_verify_all_four_presentations_and_allow_viewer() -> None:
    application, actor, ids, objects = _application()
    _seed_result(application, actor, ids, objects)
    with ids["sessions"].begin() as session:
        session.execute(workspace_member.insert().values(
            id=uuid4(), tenant_id=actor.tenant_id, workspace_id=ids["workspace_id"], actor_id="viewer",
            role="VIEWER", created_at=datetime.now(UTC),
        ))
    viewer = Actor(actor.tenant_id, "viewer")

    result = application.get_result(viewer, ids["run_id"])
    assert result["schema_version"] == "1.0.5"
    assert result["presentation"]["version"] == "0.4"
    for variant in ("summary", "full"):
        for report_format in ("html", "markdown"):
            report = application.get_report(
                viewer, ids["run_id"], variant=variant, report_format=report_format
            )
            assert report["content"]
            assert hashlib.sha256(report["content"].encode()).hexdigest() == report["content_sha256"]


def test_dual_report_rejects_cross_tenant_and_content_hash_mismatch() -> None:
    application, actor, ids, objects = _application()
    _seed_result(application, actor, ids, objects)
    with pytest.raises(NotFoundError):
        application.get_report(Actor(uuid4(), "viewer"), ids["run_id"], variant="summary", report_format="html")

    with ids["sessions"].begin() as session:
        row = session.execute(skill_result.select()).mappings().one()
        summary = dict(row["summary"])
        summary["presentation"]["summary"]["html"]["content_sha256"] = "0" * 64
        session.execute(skill_result.update().where(skill_result.c.id == row["id"]).values(summary=summary))
    with pytest.raises(ArtifactIntegrityError, match="presentation hash"):
        application.get_report(actor, ids["run_id"], variant="summary", report_format="html")


def test_legacy_result_has_no_synthesized_presentation() -> None:
    application, actor, ids, objects = _application()
    _seed_result(application, actor, ids, objects)
    with ids["sessions"].begin() as session:
        session.execute(skill_result.update().values(schema_version="1.0.4", summary={"result_summary": "legacy"}))

    assert application.get_result(actor, ids["run_id"])["presentation"] is None
    with pytest.raises(NotFoundError, match="unavailable"):
        application.get_report(actor, ids["run_id"], variant="full", report_format="markdown")


def test_report_rejects_database_and_object_digest_mismatch() -> None:
    application, actor, ids, objects = _application()
    _seed_result(application, actor, ids, objects)
    with ids["sessions"].begin() as session:
        session.execute(skill_result.update().values(sha256="0" * 64))

    with pytest.raises(ArtifactIntegrityError, match="object metadata"):
        application.get_report(actor, ids["run_id"], variant="full", report_format="html")


def test_presentation_gate_requires_aliases_complete_set_and_one_megabyte_limit() -> None:
    base = {
        "status": "completed",
        "structured_output": {
            "target_user_definition": {"admitted": True},
            "summary_report": "summary",
            "summary_report_html": "<p>summary</p>",
            "full_report": "full",
            "full_report_html": "<p>full</p>",
            "human_report": "summary",
            "human_report_html": "<p>summary</p>",
        },
    }
    metadata = UserValidationApplication._presentation_metadata(base)
    assert metadata["version"] == "0.4"
    assert UserValidationApplication._presentation_metadata(
        {"status": "blocked", "structured_output": {}}
    ) is None

    base["structured_output"]["human_report"] = "different"
    with pytest.raises(ValueError, match="human_report"):
        UserValidationApplication._presentation_metadata(base)
    base["structured_output"]["human_report"] = "summary"
    base["structured_output"]["full_report"] = "x" * 1_048_577
    with pytest.raises(ReportTooLargeError):
        UserValidationApplication._presentation_metadata(base)


def test_report_http_endpoint_is_no_store_and_returns_integrity_fields() -> None:
    application, actor, ids, objects = _application()
    _seed_result(application, actor, ids, objects)
    app = create_app()
    app.state.user_validation_application = application

    response = TestClient(app).get(
        f"/api/v1/runs/{ids['run_id']}/user-validation-reports/summary?format=html",
        headers={"X-Tenant-Id": str(actor.tenant_id), "X-Actor-Id": actor.actor_id},
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["skill_result_sha256"]
    assert response.json()["content_sha256"] == hashlib.sha256(
        response.json()["content"].encode()
    ).hexdigest()
