from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select

from launchscope_api.infrastructure.db.schema import (
    agent_plan,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    material,
    material_analysis,
    material_read_receipt,
    material_selection,
    material_selection_item,
    material_unit,
    run_execution_control,
    run_manifest,
    stage,
    task,
    task_material_scope,
)
from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.modules.evidence.mcp_application import (
    MaterialIntegrityFailed,
    MaterialScopeDenied,
    McpEvidenceApplication,
)
from launchscope_api.modules.identity_tenant.application import Actor


class _Objects:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes:
        return self.values[object_key][:max_bytes]


def _seed(database: Engine, tenant_records: dict[str, Any]) -> tuple[UUID, UUID, str, _Objects]:
    now = datetime.now(UTC)
    tenant_id = tenant_records["tenant_id"]
    run_id = tenant_records["run_id"]
    version_id = tenant_records["version_id"]
    stage_id, task_id, plan_id = uuid4(), uuid4(), uuid4()
    material_id, analysis_id, unit_id, scope_id = uuid4(), uuid4(), uuid4(), uuid4()
    object_key = f"tenant/{tenant_id}/material-unit/{unit_id}.json"
    body = json.dumps({"content": "bounded source text", "visual_summary": None}).encode()
    digest = hashlib.sha256(body).hexdigest()
    unit_ref = f"material-unit:{unit_id}@{digest}"
    with database.begin() as connection:
        connection.execute(
            evaluation_run.update().where(evaluation_run.c.id == run_id).values(status="RUNNING")
        )
        connection.execute(
            run_execution_control.update()
            .where(run_execution_control.c.tenant_id == tenant_id, run_execution_control.c.run_id == run_id)
            .values(state="ACTIVE", control_epoch=0, usage_settlement_status="NONE", in_flight_count=0, updated_at=now)
        )
        connection.execute(
            stage.insert().values(
                id=stage_id, tenant_id=tenant_id, run_id=run_id, code="DOMAIN_REVIEW", ordinal=2,
                status="RUNNING",
            )
        )
        connection.execute(
            task.insert().values(
                id=task_id, tenant_id=tenant_id, run_id=run_id, stage_id=stage_id,
                stage_code="DOMAIN_REVIEW", agent_identity_ref="user-evidence@5.0",
                skill_ref="user-evidence", skill_version="5.0", status="RUNNING",
                idempotency_key=f"material-read-{task_id}", dependencies=[],
                tool_allowlist=["launchscope-context.get.v2", "material.read.v1"], timeout_seconds=60,
                success_condition={}, evidence_requirement="cite assigned material", required=True,
                correction_attempts=0, transient_retries=0, dispatch_epoch=0, side_effect_started=False,
                created_at=now, updated_at=now,
            )
        )
        connection.execute(
            agent_plan.insert().values(
                id=plan_id, tenant_id=tenant_id, run_id=run_id, planning_task_id=task_id,
                dispatch_epoch=0, plan_version=1, evaluation_mode="STANDARD", raw_plan={},
                plan_sha256="1" * 64, status="ACCEPTED", created_at=now, decided_at=now,
            )
        )
        connection.execute(
            material.insert().values(
                id=material_id, tenant_id=tenant_id, product_version_id=version_id,
                source_type="UPLOAD", object_key=f"tenant/{tenant_id}/source.txt", sha256="2" * 64,
                size_bytes=10, mime_type="text/plain", display_name="source.txt", trust_level="T1",
                ingest_status="VALIDATED", object_metadata={}, submitted_at=now, created_at=now,
            )
        )
        connection.execute(
            material_analysis.insert().values(
                id=analysis_id, tenant_id=tenant_id, material_id=material_id, product_version_id=version_id,
                status="READY", attempt=0, parser_version="test", page_count=1, unit_count=1,
                coverage={"total": 1, "parsed": 1, "visual_inspected": 0, "uncovered_locators": []},
                external_consent=False, created_at=now, updated_at=now, completed_at=now,
            )
        )
        connection.execute(
            material_unit.insert().values(
                id=unit_id, tenant_id=tenant_id, analysis_id=analysis_id, material_id=material_id,
                product_version_id=version_id, ordinal=1, unit_type="PARAGRAPH", locator={"paragraph": 1},
                tags=["product"], confidence=1, contains_sensitive_data=False, object_key=object_key,
                sha256=digest, summary="bounded source text", created_at=now,
            )
        )
        connection.execute(
            task_material_scope.insert().values(
                id=scope_id, tenant_id=tenant_id, run_id=run_id, task_id=task_id, plan_id=plan_id,
                material_id=material_id, analysis_id=analysis_id, unit_ids=[str(unit_id)],
                unit_refs=[unit_ref], reason="test assignment", required=True, scope_sha256="3" * 64,
                created_at=now,
            )
        )
    return task_id, unit_id, unit_ref, _Objects({object_key: body})


def test_material_read_is_scoped_and_receipted(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    task_id, _unit_id, unit_ref, objects = _seed(database, tenant_records)
    actor = Actor(tenant_records["tenant_id"], "agent:user-evidence")
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, objects))

    result = application.material_read(actor, tenant_records["run_id"], task_id, [unit_ref], "verify claim")

    units = cast(list[dict[str, object]], result["units"])
    assert units[0]["content"] == "bounded source text"
    locator = cast(dict[str, object], units[0]["source_locator"])
    assert UUID(str(locator["source_locator_id"]))
    assert locator["evidence_id"] == units[0]["evidence_id"]
    assert locator["source_kind"] == "INTERNAL_MATERIAL"
    assert locator["title"] == "source.txt"
    assert locator["locator"] == {"paragraph": 1}
    assert locator["content_sha256"] == units[0]["sha256"]
    with database.connect() as connection:
        receipt = connection.execute(
            select(material_read_receipt.c.status).where(material_read_receipt.c.task_id == task_id)
        ).scalar_one()
    assert receipt == "SUCCEEDED"


def test_manager_planning_context_includes_selected_material_before_task_scopes_exist(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    now = datetime.now(UTC)
    tenant_id = tenant_records["tenant_id"]
    run_id = tenant_records["run_id"]
    version_id = tenant_records["version_id"]
    stage_id, task_id = uuid4(), uuid4()
    material_id, analysis_id, unit_id, selection_id = uuid4(), uuid4(), uuid4(), uuid4()
    digest = "a" * 64
    with database.begin() as connection:
        connection.execute(evaluation_run.update().where(evaluation_run.c.id == run_id).values(status="RUNNING"))
        connection.execute(run_manifest.insert().values(
            run_id=run_id, tenant_id=tenant_id,
            frozen_config={
                "architecture_generation": "supervisor-1p4-report-v3",
                "report_preferences": {"locale": "zh-CN"},
            },
            manifest_sha256="5" * 64, budget={}, security_policy={}, created_at=now,
        ))
        connection.execute(stage.insert().values(
            id=stage_id, tenant_id=tenant_id, run_id=run_id, code="LEADER_PLANNING", ordinal=1,
            status="RUNNING",
        ))
        connection.execute(task.insert().values(
            id=task_id, tenant_id=tenant_id, run_id=run_id, stage_id=stage_id,
            stage_code="LEADER_PLANNING", agent_identity_ref="evaluation-manager@6.0",
            skill_ref="evaluation-manager", skill_version="6.0", status="RUNNING",
            idempotency_key=f"planning-material-{task_id}", dependencies=[],
            tool_allowlist=["launchscope-context.get.v2"], timeout_seconds=60,
            success_condition={}, evidence_requirement="route selected material", required=True,
            correction_attempts=0, transient_retries=0, dispatch_epoch=0, side_effect_started=False,
            created_at=now, updated_at=now,
        ))
        connection.execute(material.insert().values(
            id=material_id, tenant_id=tenant_id, product_version_id=version_id,
            source_type="UPLOAD", object_key=f"tenant/{tenant_id}/source.txt", sha256="2" * 64,
            size_bytes=10, mime_type="text/plain", display_name="source.txt", trust_level="T1",
            ingest_status="VALIDATED", object_metadata={}, submitted_at=now, created_at=now,
        ))
        connection.execute(material_analysis.insert().values(
            id=analysis_id, tenant_id=tenant_id, material_id=material_id, product_version_id=version_id,
            status="READY", attempt=0, parser_version="test", page_count=1, unit_count=1,
            coverage={"total": 1, "parsed": 1, "visual_inspected": 0, "uncovered_locators": []},
            external_consent=False, created_at=now, updated_at=now, completed_at=now,
        ))
        connection.execute(material_unit.insert().values(
            id=unit_id, tenant_id=tenant_id, analysis_id=analysis_id, material_id=material_id,
            product_version_id=version_id, ordinal=1, unit_type="PARAGRAPH", locator={"paragraph": 1},
            tags=["product"], confidence=1, contains_sensitive_data=False,
            object_key=f"tenant/{tenant_id}/material-unit/{unit_id}.json", sha256=digest,
            summary="selected product material", created_at=now,
        ))
        connection.execute(material_selection.insert().values(
            id=selection_id, tenant_id=tenant_id, product_version_id=version_id, revision=1,
            idempotency_key=f"planning-selection-{selection_id}", request_sha256="3" * 64,
            object_key=f"tenant/{tenant_id}/selection.json", sha256="4" * 64,
            confirmed_by="local-demo:test", confirmed_at=now, created_at=now,
        ))
        connection.execute(material_selection_item.insert().values(
            id=uuid4(), tenant_id=tenant_id, selection_id=selection_id, material_id=material_id,
            analysis_id=analysis_id, decision="INCLUDE", acknowledged_uncovered_locators=[], created_at=now,
        ))
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, _Objects({})))

    result = application.context_get_v2(
        Actor(tenant_id, "agent:evaluation-manager"), run_id, task_id
    )

    assert result["material_scope"] == []
    assert result["report_preferences"] == {"locale": "zh-CN"}
    assert [item["unit_ref"] for item in result["material_catalog"]] == [
        f"material-unit:{unit_id}@{digest}"
    ]


def test_material_read_denies_unassigned_unit_and_records_receipt(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    task_id, _unit_id, _unit_ref, objects = _seed(database, tenant_records)
    actor = Actor(tenant_records["tenant_id"], "agent:user-evidence")
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, objects))
    denied_ref = f"material-unit:{uuid4()}@{'4' * 64}"

    with pytest.raises(MaterialScopeDenied, match="MATERIAL_SCOPE_DENIED"):
        application.material_read(actor, tenant_records["run_id"], task_id, [denied_ref], "verify claim")

    with database.connect() as connection:
        receipt = connection.execute(
            select(material_read_receipt.c.status).where(material_read_receipt.c.task_id == task_id)
        ).scalar_one()
    assert receipt == "SCOPE_DENIED"


def test_material_read_denies_completed_task(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    task_id, _unit_id, unit_ref, objects = _seed(database, tenant_records)
    with database.begin() as connection:
        connection.execute(task.update().where(task.c.id == task_id).values(status="SUCCEEDED"))
    actor = Actor(tenant_records["tenant_id"], "agent:user-evidence")
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, objects))

    with pytest.raises(MaterialScopeDenied, match="MATERIAL_SCOPE_DENIED"):
        application.material_read(actor, tenant_records["run_id"], task_id, [unit_ref], "late read")


def test_material_read_fails_closed_on_object_tamper(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    task_id, _unit_id, unit_ref, objects = _seed(database, tenant_records)
    object_key = next(iter(objects.values))
    objects.values[object_key] = b'{"content":"tampered"}'
    actor = Actor(tenant_records["tenant_id"], "agent:user-evidence")
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, objects))

    with pytest.raises(MaterialIntegrityFailed, match="MATERIAL_INTEGRITY_FAILED"):
        application.material_read(actor, tenant_records["run_id"], task_id, [unit_ref], "verify integrity")

    with database.connect() as connection:
        receipt = connection.execute(
            select(material_read_receipt.c.status).where(material_read_receipt.c.task_id == task_id)
        ).scalar_one()
        run_status = connection.execute(
            select(evaluation_run.c.status).where(evaluation_run.c.id == tenant_records["run_id"])
        ).scalar_one()
    assert receipt == "INTEGRITY_FAILED"
    assert run_status == "NEEDS_ATTENTION"


def test_context_v2_excludes_legacy_material_payload_before_enforcing_budget(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    task_id, _unit_id, _unit_ref, objects = _seed(database, tenant_records)
    now = datetime.now(UTC)
    rows = []
    for index in range(40):
        evidence_id = uuid4()
        object_key = f"tenant/{tenant_records['tenant_id']}/analysis/{evidence_id}.json"
        body = json.dumps(
            {
                "page_count": 1,
                "model_context": "material context " * 300,
                "source": {"file_name": f"material-{index}.pdf", "sha256": "4" * 64},
            }
        ).encode()
        digest = hashlib.sha256(body).hexdigest()
        objects.values[object_key] = body
        rows.append(
            {
                "id": evidence_id,
                "tenant_id": tenant_records["tenant_id"],
                "run_id": tenant_records["run_id"],
                "task_id": task_id,
                "source_type": "UPLOAD",
                "object_key": object_key,
                "sha256": digest,
                "size_bytes": len(body),
                "mime_type": "application/vnd.launchscope.material-analysis+json",
                "evidence_level": "E1",
                "trust_level": "T1",
                "summary": "large material summary " * 80,
                "simulated": False,
                "fetched_at": now,
                "created_at": now,
            }
        )
    with database.begin() as connection:
        connection.execute(
            run_manifest.insert().values(
                run_id=tenant_records["run_id"],
                tenant_id=tenant_records["tenant_id"],
                frozen_config={},
                manifest_sha256="5" * 64,
                budget={},
                security_policy={},
                created_at=now,
            )
        )
        connection.execute(evidence.insert(), rows)
    actor = Actor(tenant_records["tenant_id"], "agent:user-evidence")
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, objects))

    result = application.context_get_v2(actor, tenant_records["run_id"], task_id)

    assert "material_context" not in result
    assert result["tenant_id"] == str(tenant_records["tenant_id"])
    assert result["project_id"] == str(tenant_records["project_id"])
    assert result["product_version_id"] == str(tenant_records["version_id"])
    assert result["product_title"] == "T4 test project"
    assert len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")) <= 40_000


def test_auditor_context_includes_findings_already_audited_in_prior_round(
    database: Engine, runtime_engine: Engine, tenant_records: dict[str, Any]
) -> None:
    task_id, _unit_id, _unit_ref, objects = _seed(database, tenant_records)
    now = datetime.now(UTC)
    finding_id = uuid4()
    source = {
        "finding_id": str(finding_id),
        "dimension": "USER_EVIDENCE",
        "statement": "Prior-round finding remains in the re-audit identity lock.",
    }
    source_sha = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    with database.begin() as connection:
        connection.execute(
            task.update()
            .where(task.c.id == task_id)
            .values(stage_code="EVIDENCE_AUDIT", agent_identity_ref="evidence-auditor@6.0")
        )
        connection.execute(
            run_manifest.insert().values(
                run_id=tenant_records["run_id"],
                tenant_id=tenant_records["tenant_id"],
                frozen_config={"report_preferences": {"locale": "zh-CN"}},
                manifest_sha256="5" * 64,
                budget={},
                security_policy={},
                created_at=now,
            )
        )
        connection.execute(
            finding.insert().values(
                id=finding_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                task_id=task_id,
                dimension_code="USER_EVIDENCE",
                grade="MODERATE",
                claim_type="OBSERVATION",
                statement=source["statement"],
                is_hypothesis=False,
                submitted_by="user-evidence",
                submitted_at=now,
                supersedes_id=None,
                structured_result={"finding": source},
                simulated=False,
                hard_block=False,
                block_reason=None,
            )
        )
        connection.execute(
            evidence_audit.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                finding_id=finding_id,
                decision="ACCEPT",
                auditor_id="evidence-auditor@6.0",
                reason="accepted in round one",
                contract_version="4.0",
                rule_ids=[],
                referenced_evidence_ids=[],
                score_components={},
                flags=[],
                source_finding_sha256=source_sha,
                audit_round=1,
                remediation_target=None,
                audited_at=now,
            )
        )
    application = McpEvidenceApplication(session_factory(runtime_engine), cast(Any, objects))

    result = application.context_get_v2(
        Actor(tenant_records["tenant_id"], "agent:evidence-auditor"),
        tenant_records["run_id"],
        task_id,
    )

    assert result["audit_identity_lock"] == [
        {"ordinal": 1, "finding_id": str(finding_id), "source_finding_sha256": source_sha}
    ]
