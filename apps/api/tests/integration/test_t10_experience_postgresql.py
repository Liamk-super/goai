"""T10 durable read-model evidence; no in-memory control-plane state is used."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from launchscope_api.infrastructure.db.schema import (
    agent_report_artifact,
    decision,
    evaluation_run,
    evidence,
    product_profile,
    product_version,
    project,
    public_demo_disclosure_acceptance,
    public_demo_share,
    report,
    stage,
    task,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import create_database_engine, session_factory
from launchscope_api.main import PersistentControlPlane, create_app
from launchscope_api.modules.experience.public_share import PublicDemoShareResolver
from launchscope_api.modules.experience.read_model import ExperienceReadApplication
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.material_ingestion import InMemoryQuarantineObjectStore, ObjectMetadata
from launchscope_api.modules.project_dossier.persistent_application import (
    PersistentIdentityTenantApplication,
    PersistentProjectDossierApplication,
)


@dataclass
class _ReportObjects:
    bodies: dict[str, bytes]

    def __post_init__(self) -> None:
        self.settings = SimpleNamespace(presign_ttl_seconds=120)

    def head(self, object_key: str) -> ObjectMetadata | None:
        body = self.bodies.get(object_key)
        if body is None:
            return None
        return ObjectMetadata(sha256(body).hexdigest(), len(body), "application/json", "test", {})

    def get_private(self, object_key: str, *, max_bytes: int) -> bytes:
        body = self.bodies[object_key]
        assert len(body) <= max_bytes
        return body

    def signed_read_url(self, object_key: str) -> str:
        return f"memory://public/{object_key}"


def _claim() -> dict[str, object]:
    return {
        "claim_id": "claim-summary",
        "section": "HIGHLIGHT",
        "text": "该判断仍需更多证据验证",
        "status": "PENDING_VALIDATION",
        "decision_relevance": "CONTEXT",
        "citation_ids": [],
        "score_bearing": False,
    }


def _specialist_document(records, report_id, agent_code: str) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "report_id": str(report_id),
        "run_id": str(records["run_id"]),
        "project_id": str(records["project_id"]),
        "product_version_id": str(records["version_id"]),
        "product_title": "T4 test project",
        "agent_code": agent_code,
        "source_sha256": "b" * 64,
        "executive_summary": ["claim-summary"],
        "metrics": [],
        "claims": [_claim()],
        "domain_payload": {"scope": agent_code},
        "risks": [],
        "actions": [],
        "citations": [],
        "source_directory": [],
        "audit_summary": {"verified": 0, "insufficient": 1, "needs_more": 1, "conflicted": 0},
        "raw_audit_refs": [f"audit:{agent_code}"],
    }


def _supervisor_document(records, report_id, specialist_ids) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "report_id": str(report_id),
        "run_id": str(records["run_id"]),
        "project_id": str(records["project_id"]),
        "product_version_id": str(records["version_id"]),
        "product_title": "T4 test project",
        "source_sha256": "a" * 64,
        "top_card": {
            "potential_index": 68,
            "stage": "早期验证",
            "confidence_band": "MEDIUM",
            "evidence_coverage": 0.5,
            "recommendation": "VALIDATE_FURTHER",
        },
        "summary_claim_id": "claim-summary",
        "claims": [_claim()],
        "highlights": ["claim-summary"],
        "critical_issues": [],
        "role_summaries": {"user": [], "product": [], "investment": []},
        "cross_domain_claims": [],
        "actions": [
            {
                "action_id": "action-validate",
                "title": "补充验证",
                "owner": "项目负责人",
                "deadline_days": 14,
                "success_criteria": ["取得可审计证据"],
                "failure_triggers": ["证据无法复现"],
                "required_evidence": ["访谈记录"],
                "related_claim_ids": ["claim-summary"],
            }
        ],
        "confidence_breakdown": {
            "profile_ref": "confidence@1.0",
            "audited_evidence_quality": 0.5,
            "evidence_coverage": 0.5,
            "independent_source_support": 0.5,
            "freshness": 0.5,
            "cross_domain_agreement": 0.5,
            "unresolved_conflict_penalty": 0.1,
            "score": 0.5,
            "band": "MEDIUM",
        },
        "agent_report_cards": [
            {
                "agent_code": agent_code,
                "report_id": str(specialist_ids[agent_code]),
                "title": f"{agent_code} report",
                "summary_claim_ids": ["claim-summary"],
                "source_sha256": "b" * 64,
            }
            for agent_code in specialist_ids
        ],
        "citations": [],
        "source_directory": [],
        "audit_detail_ref": "evidence-auditor",
    }


def _client(database, runtime_engine, tenant_records, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    with database.begin() as connection:
        connection.execute(
            workspace_member.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                workspace_id=tenant_records["workspace_id"],
                actor_id="alice",
                role="OWNER",
            )
        )
    ops_engine = create_database_engine(
        database.url.render_as_string(hide_password=False), application_role="launchscope_ops"
    )
    app = create_app()
    app.state.experience_read_model = ExperienceReadApplication(
        session_factory(runtime_engine), ops_sessions=session_factory(ops_engine)
    )
    monkeypatch.setenv("LAUNCHSCOPE_OPS_AUDIT_ACTORS", "ops-auditor")
    headers = {
        "X-Tenant-Id": str(tenant_records["tenant_id"]),
        "X-Actor-Id": "alice",
        "X-Correlation-Id": str(uuid4()),
    }
    return TestClient(app), headers


def test_workspace_run_projection_and_sse_cursor_are_backed_by_postgresql(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    client, headers = _client(database, runtime_engine, tenant_records, monkeypatch)
    project_response = client.get("/api/v1/projects", headers=headers)
    assert project_response.status_code == 200, project_response.text
    assert project_response.json()["items"][0]["project_id"] == str(tenant_records["project_id"])

    run_id = tenant_records["run_id"]
    run_response = client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert run_response.status_code == 200, run_response.text
    assert run_response.json()["current_cursor"] == "event.initial"

    stream = client.get(f"/api/v1/runs/{run_id}/events", headers=headers)
    assert stream.status_code == 200, stream.text
    assert "event: run.snapshot" in stream.text
    assert "DRAFT" in stream.text

    invalid = client.get(f"/api/v1/runs/{run_id}/events?cursor=event.not-a-uuid", headers=headers)
    assert invalid.status_code == 409
    assert invalid.json()["error_code"] == "CURSOR_INVALID"


def test_project_portrait_remains_visible_without_a_formal_run(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    client, headers = _client(database, runtime_engine, tenant_records, monkeypatch)
    project_id = uuid4()
    version_id = uuid4()
    confirmed_fields = {
        "one_line_value_claim": "让研究生完成论文录用准备",
        "problem": "论文写作与录用支持分散",
        "core_features": "选题、写作和投稿辅助",
        "inspectable_materials": "https://example.test/demo",
        "team": "两位全职创始人",
        "stage": "只有想法",
        "target_user": "准备投稿的研究生",
        "payer": "研究生本人",
        "validation_goal": "确认 Demo 是否值得制作",
        "region": "中国香港",
        "timing": "2026 年秋季",
    }
    with database.begin() as connection:
        connection.execute(
            project.insert().values(
                id=project_id,
                tenant_id=tenant_records["tenant_id"],
                workspace_id=tenant_records["workspace_id"],
                name="Portrait-only project",
                dossier_status="ACTIVE",
            )
        )
        connection.execute(
            product_version.insert().values(
                id=version_id,
                tenant_id=tenant_records["tenant_id"],
                project_id=project_id,
                version_number=1,
                label="V1",
                stage="DRAFT",
                status="DRAFT",
            )
        )
        connection.execute(
            product_profile.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                product_version_id=version_id,
                confirmed_fields=confirmed_fields,
                confirmation_status="CONFIRMED",
                confirmed_by="alice",
            )
        )

    response = client.get(f"/api/v1/projects/{project_id}/portrait", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["product_version_id"] == str(version_id)
    assert response.json()["confirmed_fields"] == confirmed_fields
    assert client.get(f"/api/v1/projects/{project_id}/runs", headers=headers).json()["items"] == []


def test_workspace_and_ops_identity_domains_are_separate_and_ops_is_redacted(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    client, headers = _client(database, runtime_engine, tenant_records, monkeypatch)
    run_id = tenant_records["run_id"]
    blocked = client.get(f"/api/v1/ops/audit/runs/{run_id}", headers=headers)
    assert blocked.status_code == 403

    ops = client.get(f"/api/v1/ops/audit/runs/{run_id}", headers={"X-Ops-Actor-Id": "ops-auditor"})
    assert ops.status_code == 200, ops.text
    assert set(ops.json()).isdisjoint({"report", "material", "evidence", "finding", "prompt", "private_reasoning"})

    other_actor_headers = {**headers, "X-Actor-Id": "mallory"}
    not_visible = client.get(f"/api/v1/runs/{run_id}", headers=other_actor_headers)
    assert not_visible.status_code == 404


def test_agent_report_catalog_has_four_slots_without_bodies_and_enforces_membership(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    client, headers = _client(database, runtime_engine, tenant_records, monkeypatch)
    now = datetime.now(UTC)
    stage_id, task_id = uuid4(), uuid4()
    with database.begin() as connection:
        connection.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                code="DOMAIN_REVIEW",
                ordinal=1,
                status="COMPLETED",
                started_at=now,
                completed_at=now,
            )
        )
        connection.execute(
            task.insert().values(
                id=task_id,
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                stage_id=stage_id,
                agent_identity_id=None,
                skill_version_id=None,
                stage_code="DOMAIN_REVIEW",
                agent_identity_ref="user-evidence@4.0",
                skill_ref="user-validation-designer",
                skill_version="1.0.5",
                status="SUCCEEDED",
                lease_token=None,
                idempotency_key=f"agent-report-{task_id}",
                dependencies=[],
                tool_allowlist=[],
                budget_slice={"suggested_usd": 0},
                timeout_seconds=600,
                success_condition=["immutable report"],
                evidence_requirement="SHA-bound report",
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
        connection.execute(
            agent_report_artifact.insert().values(
                id=uuid4(),
                tenant_id=tenant_records["tenant_id"],
                run_id=tenant_records["run_id"],
                task_id=task_id,
                agent_code="user-evidence",
                report_kind="DOMAIN",
                revision=0,
                object_key=f"private/{task_id}.json",
                sha256="c" * 64,
                mime_type="application/json",
                status="AVAILABLE",
                created_at=now,
            )
        )

    response = client.get(
        f"/api/v1/experience/runs/{tenant_records['run_id']}/agent-reports",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["reports"]) == 4
    assert "content" not in response.text
    assert next(item for item in response.json()["reports"] if item["agent_code"] == "user-evidence")[
        "status"
    ] == "AVAILABLE"

    blocked = client.get(
        f"/api/v1/experience/runs/{tenant_records['run_id']}/agent-reports",
        headers={**headers, "X-Actor-Id": "mallory"},
    )
    assert blocked.status_code == 404


def test_v22_private_and_public_reports_are_one_hash_verified_run_scoped_graph(
    database, runtime_engine, tenant_records, monkeypatch
) -> None:
    client, headers = _client(database, runtime_engine, tenant_records, monkeypatch)
    now = datetime.now(UTC)
    tenant_id = tenant_records["tenant_id"]
    run_id = tenant_records["run_id"]
    report_id, decision_id, stage_id = uuid4(), uuid4(), uuid4()
    agent_codes = ("user-evidence", "product-engineering", "business-investment", "evidence-auditor")
    specialist_ids = {agent_code: uuid4() for agent_code in agent_codes}
    specialist_documents = {
        agent_code: _specialist_document(tenant_records, specialist_ids[agent_code], agent_code)
        for agent_code in agent_codes
    }
    supervisor_document = _supervisor_document(tenant_records, report_id, specialist_ids)
    bodies: dict[str, bytes] = {}
    supervisor_key = f"private/{run_id}/supervisor.json"
    bodies[supervisor_key] = json.dumps(supervisor_document, ensure_ascii=False, separators=(",", ":")).encode()
    evidence_body = b"public evidence"
    evidence_key = f"private/{run_id}/evidence.txt"
    bodies[evidence_key] = evidence_body
    tasks: dict[str, UUID] = {}
    for agent_code, document in specialist_documents.items():
        key = f"private/{run_id}/{agent_code}.json"
        bodies[key] = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()

    share_token = f"v22-public-demo-share-token-{run_id}"
    revoked_token = f"v22-public-demo-revoked-token-{run_id}"
    evidence_id = uuid4()
    other_run_id, other_evidence_id = uuid4(), uuid4()
    with database.begin() as connection:
        connection.execute(
            update(evaluation_run)
            .where(evaluation_run.c.id == run_id)
            .values(
                status="COMPLETED",
                current_stage="COMPLETED",
                state_flags={"architecture_generation": "supervisor-1p4-report-v22"},
                report_profile_ref="supervisor-report@2.0",
            )
        )
        connection.execute(
            decision.insert().values(
                id=decision_id,
                tenant_id=tenant_id,
                run_id=run_id,
                recommendation="VALIDATE_FURTHER",
                standard_version="1.0",
                dimension_grades={},
                hard_blocks=[],
                supersedes_id=None,
                created_at=now,
            )
        )
        connection.execute(
            report.insert().values(
                id=report_id,
                tenant_id=tenant_id,
                run_id=run_id,
                decision_id=decision_id,
                object_key=supervisor_key,
                sha256=sha256(bodies[supervisor_key]).hexdigest(),
                status="COMMITTED",
                action_items=[],
                supersedes_id=None,
                created_at=now,
            )
        )
        connection.execute(
            stage.insert().values(
                id=stage_id,
                tenant_id=tenant_id,
                run_id=run_id,
                code="REPORT_COMMIT",
                ordinal=5,
                status="COMPLETED",
                started_at=now,
                completed_at=now,
            )
        )
        for agent_code in agent_codes:
            task_id = tasks[agent_code] = uuid4()
            connection.execute(
                task.insert().values(
                    id=task_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    stage_id=stage_id,
                    agent_identity_id=None,
                    skill_version_id=None,
                    stage_code="REPORT_COMMIT",
                    agent_identity_ref=f"{agent_code}@6.0",
                    skill_ref="report-v22",
                    skill_version="2.2.0",
                    status="SUCCEEDED",
                    lease_token=None,
                    idempotency_key=f"v22-agent-report-{task_id}",
                    dependencies=[],
                    tool_allowlist=[],
                    budget_slice={"suggested_usd": 0},
                    timeout_seconds=600,
                    success_condition=["canonical report"],
                    evidence_requirement="SHA-bound report",
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
            key = f"private/{run_id}/{agent_code}.json"
            connection.execute(
                agent_report_artifact.insert().values(
                    id=specialist_ids[agent_code],
                    tenant_id=tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    agent_code=agent_code,
                    report_kind="AUDIT" if agent_code == "evidence-auditor" else "DOMAIN",
                    revision=1,
                    object_key=key,
                    sha256=sha256(bodies[key]).hexdigest(),
                    mime_type="application/json",
                    status="AVAILABLE",
                    created_at=now,
                )
            )
        connection.execute(
            evidence.insert().values(
                id=evidence_id,
                tenant_id=tenant_id,
                run_id=run_id,
                task_id=tasks["evidence-auditor"],
                material_id=None,
                source_type="MATERIAL",
                object_key=evidence_key,
                sha256=sha256(evidence_body).hexdigest(),
                size_bytes=len(evidence_body),
                mime_type="text/plain",
                evidence_level="E1",
                trust_level="E1",
                summary="public evidence",
                simulated=True,
                created_at=now,
            )
        )
        connection.execute(
            evaluation_run.insert().values(
                id=other_run_id,
                tenant_id=tenant_id,
                project_id=tenant_records["project_id"],
                product_version_id=tenant_records["version_id"],
                status="COMPLETED",
                current_stage="COMPLETED",
                state_flags={"architecture_generation": "supervisor-1p4-report-v22"},
                standard_version="1.0",
                correlation_id=uuid4(),
                idempotency_key=f"other-run-{other_run_id}",
                report_profile_ref="supervisor-report@2.0",
            )
        )
        connection.execute(
            evidence.insert().values(
                id=other_evidence_id,
                tenant_id=tenant_id,
                run_id=other_run_id,
                source_type="MATERIAL",
                object_key=f"private/{other_run_id}/evidence.txt",
                sha256="e" * 64,
                size_bytes=1,
                mime_type="text/plain",
                evidence_level="E1",
                trust_level="E1",
                summary="other run",
                simulated=True,
                created_at=now,
            )
        )
        for token, status, revoked_at in (
            (share_token, "ACTIVE", None),
            (revoked_token, "REVOKED", now),
        ):
            connection.execute(
                public_demo_share.insert().values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    run_id=run_id,
                    report_id=report_id,
                    token_sha256=sha256(token.encode()).hexdigest(),
                    status=status,
                    include_agent_reports=True,
                    include_evidence=True,
                    created_at=now,
                    revoked_at=revoked_at,
                )
            )

    objects = _ReportObjects(bodies)
    client.app.state.object_store = objects
    client.app.state.public_share_resolver = PublicDemoShareResolver(session_factory(runtime_engine))

    private_report = client.get(f"/api/v1/experience/v2/runs/{run_id}/report", headers=headers)
    assert private_report.status_code == 200, private_report.text
    assert private_report.json()["document"]["top_card"]["potential_index"] == 68
    assert "comparison" not in private_report.json()["document"]
    assert len(private_report.json()["document"]["agent_report_cards"]) == 4

    catalog = client.get(f"/api/v1/experience/v2/runs/{run_id}/agent-reports", headers=headers)
    assert catalog.status_code == 200, catalog.text
    assert len(catalog.json()["reports"]) == 4 and "document" not in catalog.text
    specialist = client.get(
        f"/api/v1/experience/v2/runs/{run_id}/agent-reports/user-evidence", headers=headers
    )
    assert specialist.status_code == 200, specialist.text
    assert specialist.json()["document"]["agent_code"] == "user-evidence"

    public_report = client.get(f"/api/v1/public/demo/v2/reports/{report_id}?token={share_token}")
    assert public_report.status_code == 200, public_report.text
    assert public_report.json()["document"] == private_report.json()["document"]
    for agent_code in agent_codes:
        public_agent = client.get(f"/api/v1/public/demo/v2/agent-reports/{agent_code}?token={share_token}")
        assert public_agent.status_code == 200, public_agent.text
        assert public_agent.json()["document"]["run_id"] == str(run_id)
    public_evidence = client.get(
        f"/api/v1/public/demo/v2/evidence/{evidence_id}/read-url?token={share_token}"
    )
    assert public_evidence.status_code == 200, public_evidence.text
    assert public_evidence.json()["read_url"] == f"memory://public/{evidence_key}"

    assert client.get(f"/api/v1/public/demo/v2/reports/{report_id}?token={'x' * 32}").status_code == 404
    assert client.get(f"/api/v1/public/demo/v2/reports/{report_id}?token={revoked_token}").status_code == 404
    assert client.get(f"/api/v1/public/demo/v2/reports/{uuid4()}?token={share_token}").status_code == 404
    assert (
        client.get(f"/api/v1/public/demo/v2/evidence/{other_evidence_id}/read-url?token={share_token}").status_code
        == 404
    )

    objects.bodies[supervisor_key] = b"tampered"
    compromised = client.get(f"/api/v1/experience/v2/runs/{run_id}/report", headers=headers)
    assert compromised.status_code == 409
    assert compromised.json()["error_code"] == "ARTIFACT_INTEGRITY_MISMATCH"


def test_default_api_write_path_commits_postgresql_then_drives_sse_and_ops(
    database, runtime_engine, monkeypatch
) -> None:
    """The runtime default must not fall back to the T5 in-memory control plane."""
    import launchscope_api.main as main

    sessions = session_factory(runtime_engine)
    identity = PersistentIdentityTenantApplication(sessions)
    store = InMemoryQuarantineObjectStore()
    plane = PersistentControlPlane(
        identity=identity,
        dossier=PersistentProjectDossierApplication(sessions, identity, store),
    )
    monkeypatch.setattr(main, "_persistent_control_plane", plane)
    monkeypatch.setenv("LAUNCHSCOPE_OPS_AUDIT_ACTORS", "ops-auditor")
    ops_engine = create_database_engine(
        database.url.render_as_string(hide_password=False), application_role="launchscope_ops"
    )
    app = create_app()
    app.state.experience_read_model = ExperienceReadApplication(sessions, ops_sessions=session_factory(ops_engine))
    client = TestClient(app)

    created_tenant = client.post(
        "/api/v1/tenants",
        headers={"X-Actor-Id": "alice"},
        json={"slug": f"runtime-{uuid4()}", "workspace_name": "Runtime workspace"},
    )
    assert created_tenant.status_code == 201, created_tenant.text
    tenant_id = created_tenant.json()["tenant_id"]
    workspace_id = created_tenant.json()["workspace_id"]
    headers = {
        "X-Tenant-Id": tenant_id,
        "X-Actor-Id": "alice",
        "X-Correlation-Id": str(uuid4()),
    }

    project = client.post("/api/v1/projects", headers=headers, json={"workspace_id": workspace_id, "name": "Widget"})
    assert project.status_code == 201, project.text
    version = client.post(
        f"/api/v1/projects/{project.json()['project_id']}/versions", headers=headers, json={"label": "V1"}
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["product_version_id"]
    disclosure = client.get(f"/api/v1/product-versions/{version_id}/public-demo-disclosure", headers=headers)
    assert disclosure.status_code == 200 and disclosure.json()["accepted"] is False
    disclosure_headers = {**headers, "Idempotency-Key": f"public-demo-disclosure:{version_id}:v1"}
    accepted = client.post(
        f"/api/v1/product-versions/{version_id}/public-demo-disclosure:accept",
        headers=disclosure_headers,
    )
    replay = client.post(
        f"/api/v1/product-versions/{version_id}/public-demo-disclosure:accept",
        headers=disclosure_headers,
    )
    assert accepted.status_code == 201 and replay.status_code == 201
    assert accepted.json()["acceptance_id"] == replay.json()["acceptance_id"]
    content = b"durable material"
    digest = sha256(content).hexdigest()
    material = client.post(
        f"/api/v1/product-versions/{version_id}/materials:initiate",
        headers=headers,
        json={"display_name": "brief.txt", "sha256": digest, "size_bytes": len(content), "mime_type": "text/plain"},
    )
    assert material.status_code == 201, material.text
    payload = material.json()
    store.stage_uploaded_object(payload["object_key"], ObjectMetadata(digest, len(content), "text/plain"))
    assert client.post(f"/api/v1/materials/{payload['material_id']}/complete", headers=headers).status_code == 200

    gaps = client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=headers)
    assert gaps.status_code == 200, gaps.text
    assert len(gaps.json()["questions"]) == 6
    answers = {
        "one_line_value_claim": "Help independent retailers find inventory risks before weekly ordering",
        "target_user": "Independent retailers",
        "payer": "Store owner",
        "stage": "Private beta",
        "region": "Hong Kong",
        "validation_goal": "Decide whether to fund pilot onboarding",
    }
    assert (
        client.post(
            f"/api/v1/product-versions/{version_id}/gap-answers",
            headers=headers,
            json={"correlation_id": gaps.json()["correlation_id"], "answers": answers},
        ).status_code
        == 200
    )
    confirmation = client.post(
        f"/api/v1/product-versions/{version_id}/profile-confirmations",
        headers=headers,
        json={"acknowledge_model_inference": True},
    )
    assert confirmation.status_code == 201, confirmation.text
    resumed_gaps = client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=headers)
    assert resumed_gaps.status_code == 200, resumed_gaps.text
    assert resumed_gaps.json()["questions"] == []
    assert resumed_gaps.json()["profile_draft"]["status"] == "CONFIRMED"
    assert resumed_gaps.json()["profile_draft"]["user_confirmed_fields"] == answers
    repeated_confirmation = client.post(
        f"/api/v1/product-versions/{version_id}/profile-confirmations",
        headers=headers,
        json={"acknowledge_model_inference": True},
    )
    assert repeated_confirmation.status_code == 201, repeated_confirmation.text
    assert repeated_confirmation.json()["profile_id"] == confirmation.json()["profile_id"]
    planned = client.post(f"/api/v1/product-versions/{version_id}/plan", headers=headers)
    assert planned.status_code == 200, planned.text
    run_id = planned.json()["run_id"]
    with database.connect() as connection:
        bound_run_id = connection.execute(
            select(public_demo_disclosure_acceptance.c.run_id).where(
                public_demo_disclosure_acceptance.c.product_version_id == UUID(version_id)
            )
        ).scalar_one()
    assert str(bound_run_id) == run_id

    run = client.get(f"/api/v1/runs/{run_id}", headers=headers)
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "PLANNED"
    stream = client.get(f"/api/v1/runs/{run_id}/events?cursor=event.initial", headers=headers)
    assert stream.status_code == 200, stream.text
    assert stream.text.count("event: run.status_changed") == 3
    assert "profile confirmed" in stream.text

    ops_events = client.get("/api/v1/ops/audit/events", headers={"X-Ops-Actor-Id": "ops-auditor"})
    assert ops_events.status_code == 200, ops_events.text
    matching = [item for item in ops_events.json()["items"] if item["run_id"] == run_id]
    assert matching and matching[0]["event_type"] == "evaluation.run.started"
    assert set(matching[0]).isdisjoint({"material", "prompt", "private_reasoning"})

    # The only runtime adapter is persistent.  A fresh actor object can read
    # the committed facts, which could not work with a request-local dictionary.
    assert not hasattr(plane.dossier, "projects")
    assert (
        app.state.experience_read_model.list_projects(Actor(UUID(tenant_id), "alice"))[0]["project_id"]
        == project.json()["project_id"]
    )


def test_idea_profile_creates_a_user_validation_run(database, runtime_engine, monkeypatch) -> None:
    import launchscope_api.main as main

    monkeypatch.setenv("LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED", "false")
    sessions = session_factory(runtime_engine)
    identity = PersistentIdentityTenantApplication(sessions)
    plane = PersistentControlPlane(
        identity=identity,
        dossier=PersistentProjectDossierApplication(sessions, identity, InMemoryQuarantineObjectStore()),
    )
    monkeypatch.setattr(main, "_persistent_control_plane", plane)
    client = TestClient(create_app())

    created_tenant = client.post(
        "/api/v1/tenants",
        headers={"X-Actor-Id": "alice"},
        json={"slug": f"idea-{uuid4()}", "workspace_name": "Idea workspace"},
    )
    assert created_tenant.status_code == 201, created_tenant.text
    headers = {
        "X-Tenant-Id": created_tenant.json()["tenant_id"],
        "X-Actor-Id": "alice",
        "X-Correlation-Id": str(uuid4()),
    }
    project_response = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"workspace_id": created_tenant.json()["workspace_id"], "name": "Idea"},
    )
    assert project_response.status_code == 201, project_response.text
    version_response = client.post(
        f"/api/v1/projects/{project_response.json()['project_id']}/versions",
        headers=headers,
        json={"label": "V1"},
    )
    assert version_response.status_code == 201, version_response.text
    version_id = version_response.json()["product_version_id"]
    gaps = client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=headers)
    assert gaps.status_code == 200, gaps.text
    answers = {
        "one_line_value_claim": "Help university students practise interview answers with targeted feedback",
        "target_user": "University students preparing for interviews",
        "payer": "The student or their university career service",
        "stage": "只有想法",
        "region": "Hong Kong",
        "validation_goal": "Decide which interview practice hypothesis to test first",
    }
    answered = client.post(
        f"/api/v1/product-versions/{version_id}/gap-answers",
        headers=headers,
        json={"correlation_id": gaps.json()["correlation_id"], "answers": answers},
    )
    assert answered.status_code == 200, answered.text
    confirmed = client.post(
        f"/api/v1/product-versions/{version_id}/profile-confirmations",
        headers=headers,
        json={"acknowledge_model_inference": True},
    )
    assert confirmed.status_code == 201, confirmed.text

    planned = client.post(
        f"/api/v1/product-versions/{version_id}/plan",
        headers=headers,
        json={"evaluation_mode": "USER_VALIDATION"},
    )
    assert planned.status_code == 200, planned.text
    with database.connect() as connection:
        state_flags = connection.execute(
            select(evaluation_run.c.state_flags).where(evaluation_run.c.id == UUID(planned.json()["run_id"]))
        ).scalar_one()
    assert state_flags["evaluation_mode"] == "USER_VALIDATION"
