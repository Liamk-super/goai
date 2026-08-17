from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from launchscope_api.infrastructure.db.schema import (
    evidence,
    evidence_source_locator,
    product_profile,
    skill_invocation,
    stage,
    task,
    tool_invocation,
    user_validation_script,
)
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.evaluation.dispatch_application import DispatchApplication
from launchscope_api.modules.evidence.mcp_application import (
    BrowserArtifact,
    BrowserCaptureFailed,
    McpEvidenceApplication,
)
from launchscope_api.modules.evidence.source_locator import (
    SourceLocatorRelationError,
    SourceLocatorRepository,
    browser_source_locator,
)
from launchscope_api.modules.identity_tenant.application import Actor


class _Objects:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        assert object_key and payload and mime_type in {"image/png", "application/json"}
        self.values[object_key] = payload
        return hashlib.sha256(payload).hexdigest()

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes:
        body = self.values[object_key]
        assert len(body) <= max_bytes
        return body


class _Browser:
    def capture(self, url: str, *, timeout_seconds: int) -> BrowserArtifact:
        assert url == "https://creatrades.com" and timeout_seconds == 120
        return BrowserArtifact(
            final_url=url,
            title="CreaTrades",
            fetched_at=datetime.now(UTC),
            dom_summary="AI creative workflow",
            screenshot=b"real-browser-snapshot",
            region="GLOBAL",
        )


class _FailingBrowser:
    def capture(self, url: str, *, timeout_seconds: int) -> BrowserArtifact:
        raise BrowserCaptureFailed("page unavailable")


def test_known_local_browser_failure_does_not_mark_run_submission_unknown(monkeypatch) -> None:
    application = McpEvidenceApplication(None, None, browser=_FailingBrowser())  # type: ignore[arg-type]
    marked_unknown: list[str] = []
    settled: list[str] = []
    monkeypatch.setattr(application, "_assert_quota", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(application, "_start_tool", lambda *_args, **_kwargs: uuid4())
    monkeypatch.setattr(
        application,
        "_settle_tool",
        lambda _actor, _run_id, _invocation_id, status, _error=None: settled.append(status),
    )
    monkeypatch.setattr(application, "_mark_unknown", lambda _actor, _run_id, reason: marked_unknown.append(reason))

    with pytest.raises(BrowserCaptureFailed, match="page unavailable"):
        application.browser_audit(Actor(uuid4(), "agent:product-engineering"), uuid4(), uuid4(), "https://creatrades.com")

    assert marked_unknown == []
    assert settled == ["FAILED"]


def test_context_exposes_frozen_authorized_url_and_browser_call_writes_tool_ledger(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    tenant_id, run_id = tenant_records["tenant_id"], tenant_records["run_id"]
    monkeypatch.setenv("LAUNCHSCOPE_AUTHORIZED_CASE_URL", "https://creatrades.com")
    monkeypatch.setenv("LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS", "creatrades.com,app.creatrades.com")
    monkeypatch.setenv("LAUNCHSCOPE_MCP_CAPABILITY_SECRET", "integration-test-capability-secret")
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    objects = _Objects()
    script_document = {
        "schema_version": "1.0",
        "product_tasks_hash": "b" * 64,
        "tasks": [
            {
                "task_key": "core-flow",
                "description": "Complete the core product flow",
                "expected_observable_outcome": "The workflow result is visible",
                "max_steps": 8,
            }
        ],
    }
    script_body = json.dumps(script_document, sort_keys=True, separators=(",", ":")).encode()
    script_sha = hashlib.sha256(script_body).hexdigest()
    script_key = f"test/{script_sha}.json"
    objects.values[script_key] = script_body
    with database.begin() as connection:
        connection.execute(text("UPDATE evaluation_run SET status='PLANNED' WHERE id=:id"), {"id": run_id})
        connection.execute(product_profile.insert().values(
            id=uuid4(),
            tenant_id=tenant_id,
            product_version_id=tenant_records["version_id"],
            confirmed_fields={
                "one_line_value_claim": "AI creative workflow",
                "target_user": "Creative teams",
                "payer": "Team owner",
                "region": "GLOBAL",
                "stage": "MVP",
                "validation_goal": "Validate the core workflow",
            },
            confirmation_status="CONFIRMED",
            confirmed_by="local-demo:test",
            confirmed_at=datetime.now(UTC),
            supersedes_id=None,
            created_at=datetime.now(UTC),
        ))
        connection.execute(user_validation_script.insert().values(
            id=uuid4(),
            tenant_id=tenant_id,
            product_version_id=tenant_records["version_id"],
            revision=1,
            object_key=script_key,
            sha256=script_sha,
            product_tasks_sha256="b" * 64,
            task_count=1,
            confirmed_by="local-demo:test",
            idempotency_key=f"mcp-script-{run_id}",
            request_sha256="c" * 64,
            confirmed_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        ))
    sessions = session_factory(runtime_engine)
    DispatchApplication(sessions, objects).dispatch(
        Actor(tenant_id, "local-demo:test"), run_id, idempotency_key="mcp-ledger"
    )

    product_task = uuid4()
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        stage_id = session.execute(select(stage.c.id).where(
            stage.c.tenant_id == tenant_id,
            stage.c.run_id == run_id,
        ).limit(1)).scalar_one()
        session.execute(task.insert().values(
            id=product_task,
            tenant_id=tenant_id,
            run_id=run_id,
            stage_id=stage_id,
            agent_identity_id=None,
            skill_version_id=None,
            stage_code="DOMAIN_REVIEW",
            agent_identity_ref="product-engineering@5.0",
            skill_ref="browser-product-audit",
            skill_version="1.0",
            status="RUNNING",
            lease_token=None,
            idempotency_key=f"mcp-product-task-{product_task}",
            dependencies=[],
            tool_allowlist=["browser-audit.v1"],
            budget_slice={},
            timeout_seconds=600,
            success_condition={},
            evidence_requirement="traceable",
            required=True,
            correction_attempts=0,
            transient_retries=0,
            dispatch_epoch=0,
            last_failure_class=None,
            last_error=None,
            side_effect_started=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ))
        assert session.execute(select(task.c.id).where(
            task.c.tenant_id == tenant_id,
            task.c.run_id == run_id,
            task.c.agent_identity_ref.like("product-engineering@%"),
        )).scalar_one() == product_task

    actor = Actor(tenant_id, "agent:product-engineering")
    application = McpEvidenceApplication(sessions, objects, browser=_Browser())  # type: ignore[arg-type]
    context = application.context_get(actor, run_id, product_task)
    assert context["product_profile"]["one_line_value_claim"] == "AI creative workflow"
    assert context["authorized_urls"] == ["https://creatrades.com"]
    assert context["tool_allowlist"] == ["browser-audit.v1"]

    result = application.browser_audit(actor, run_id, product_task, "https://creatrades.com")
    assert result["title"] == "CreaTrades"
    returned_locator = result["source_locators"][0]
    assert returned_locator["evidence_id"] == result["evidence_id"]
    assert returned_locator["source_kind"] == "PUBLIC_URL"
    assert returned_locator["canonical_url"] == "https://creatrades.com"
    with tenant_transaction(sessions, tenant_records["scope"]) as session:
        evidence_row = session.execute(select(evidence).where(
            evidence.c.tenant_id == tenant_id,
            evidence.c.run_id == run_id,
            evidence.c.task_id == product_task,
            evidence.c.source_type == "BROWSER",
        )).mappings().one()
        locator = session.execute(select(evidence_source_locator).where(
            evidence_source_locator.c.tenant_id == tenant_id,
            evidence_source_locator.c.evidence_id == evidence_row["id"],
        )).mappings().one()
        assert locator["canonical_url"] == "https://creatrades.com"
        assert locator["title"] == "CreaTrades"
        assert locator["source_kind"] == "PUBLIC_URL"
        assert locator["screenshot_sha256"] == hashlib.sha256(b"real-browser-snapshot").hexdigest()
        with pytest.raises(SourceLocatorRelationError):
            SourceLocatorRepository().append(
                session,
                tenant_id=tenant_id,
                run_id=uuid4(),
                evidence_id=evidence_row["id"],
                locators=(
                    browser_source_locator(
                        final_url="https://creatrades.com",
                        title="CreaTrades",
                        fetched_at=datetime.now(UTC),
                        region="GLOBAL",
                        screenshot_sha256="a" * 64,
                    ),
                ),
            )
        assert session.execute(select(func.count()).select_from(skill_invocation).where(
            skill_invocation.c.tenant_id == tenant_id,
            skill_invocation.c.task_id == product_task,
        )).scalar_one() == 1
        invocation = session.execute(select(tool_invocation).where(
            tool_invocation.c.tenant_id == tenant_id,
        )).mappings().one()
        assert invocation["tool_code"] == "browser-audit.v1"
        assert invocation["status"] == "SUCCEEDED"
        assert len(invocation["parameters_sha256"]) == 64
