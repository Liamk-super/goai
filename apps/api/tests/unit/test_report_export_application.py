from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from launchscope_api.infrastructure.db.schema import agent_report_artifact, metadata, report, report_export_artifact
from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.main import ControlPlane, create_app
from launchscope_api.modules.decision_report.export_application import (
    ExportRequest,
    ExportResult,
    IdempotencyConflictError,
    ReportExportApplication,
)
from launchscope_api.modules.decision_report.export_renderer import (
    EvidenceArchiveEntry,
    PrintTarget,
    assemble_report_package,
    build_print_projection,
    report_api_marker,
)
from launchscope_api.modules.experience.api import PublicExportRateLimiter
from launchscope_api.modules.experience.public_share import PublicDemoShareGrant
from launchscope_api.modules.identity_tenant.application import Actor


def test_print_renderer_binds_the_requested_locale_before_loading_the_public_projection() -> None:
    source = Path("apps/api/src/launchscope_api/modules/decision_report/export_renderer.py").read_text(encoding="utf-8")
    assert '"name": "launchscope.locale"' in source
    assert '"value": target.locale' in source
    assert "context.add_cookies" in source


def test_print_renderer_uses_the_canonical_report_version_for_v3_web_projection() -> None:
    target = PrintTarget(
        run_id=str(uuid4()),
        report_id=str(uuid4()),
        agent_code=None,
        source_sha256="b" * 64,
        document={"schema_version": "3.0", "source_sha256": "a" * 64},
        view="FULL",
        locale="zh-CN",
    )

    projection = build_print_projection(target)

    assert projection["report_schema_version"] == "3.0"
    assert report_api_marker(target).startswith("/api/v1/public/demo/v3/reports/")


def test_full_print_renderer_opens_canonical_report_and_audit_details() -> None:
    source = Path("apps/api/src/launchscope_api/modules/decision_report/export_renderer.py").read_text(encoding="utf-8")

    assert 'details[data-export-audit="true"]' in source
    assert "details.report-v3-full-report" in source
    assert "details.report-v3-evidence-explainer" in source


class MemoryObjects:
    def __init__(self) -> None:
        self.values: dict[str, tuple[bytes, str, str]] = {}

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        digest = hashlib.sha256(payload).hexdigest()
        self.values[object_key] = (payload, digest, mime_type)
        return digest

    def get_private(self, object_key: str, *, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        body = self.values[object_key][0]
        assert len(body) <= max_bytes
        return body

    def head(self, object_key: str):
        item = self.values.get(object_key)
        if item is None:
            return None
        return SimpleNamespace(size_bytes=len(item[0]), sha256=item[1], mime_type=item[2])

    def signed_read_url(self, object_key: str) -> str:
        assert object_key in self.values
        return f"https://objects.invalid/{object_key}"


class CountingRenderer:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def render_pdf(self, target) -> bytes:
        self.calls.append(target)
        return b"%PDF-1.7\n" + target.source_sha256.encode("ascii")


def _supervisor_document(report_id, run_id) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "report_id": str(report_id),
        "run_id": str(run_id),
        "product_title": "证据助手",
        "source_sha256": "a" * 64,
        "agent_report_cards": [],
        "citations": [],
        "source_directory": [],
    }


def _application():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    sessions = session_factory(engine)
    objects = MemoryObjects()
    renderer = CountingRenderer()
    actor = Actor(uuid4(), "owner")
    run_id, report_id, decision_id = uuid4(), uuid4(), uuid4()
    body = json.dumps(_supervisor_document(report_id, run_id), sort_keys=True).encode()
    digest = objects.put_private(f"reports/{report_id}.json", body, "application/json")
    with sessions.begin() as session:
        session.execute(
            report.insert().values(
                id=report_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                decision_id=decision_id,
                object_key=f"reports/{report_id}.json",
                sha256=digest,
                status="COMMITTED",
                action_items=[],
                created_at=datetime.now(UTC),
            )
        )
    return ReportExportApplication(sessions, objects, renderer), sessions, objects, renderer, actor, report_id


def test_same_source_and_parameters_reuse_completed_artifact_without_rendering_again() -> None:
    application, sessions, _objects, renderer, actor, report_id = _application()
    request = ExportRequest(kind="SUPERVISOR", view="FULL", locale="zh-CN")

    first = application.create(actor, report_id, request, idempotency_key="export-1")
    second = application.create(actor, report_id, request, idempotency_key="export-2")

    assert first.export_id == second.export_id
    assert first.status == second.status == "COMPLETED"
    assert len(renderer.calls) == 1
    with sessions() as session:
        assert len(session.execute(select(report_export_artifact)).all()) == 1


def test_changed_canonical_sha_creates_a_new_artifact() -> None:
    application, sessions, objects, renderer, actor, report_id = _application()
    request = ExportRequest(kind="SUPERVISOR", view="FULL", locale="zh-CN")
    first = application.create(actor, report_id, request, idempotency_key="export-1")
    with sessions.begin() as session:
        row = session.execute(select(report).where(report.c.id == report_id)).mappings().one()
        document = json.loads(objects.get_private(str(row["object_key"])))
        document["source_sha256"] = "b" * 64
        body = json.dumps(document, sort_keys=True).encode()
        digest = objects.put_private(str(row["object_key"]), body, "application/json")
        session.execute(report.update().where(report.c.id == report_id).values(sha256=digest))

    second = application.create(actor, report_id, request, idempotency_key="export-2")

    assert first.export_id != second.export_id
    assert len(renderer.calls) == 2


def test_reused_idempotency_key_with_different_request_is_rejected() -> None:
    application, _sessions, _objects, _renderer, actor, report_id = _application()
    application.create(
        actor,
        report_id,
        ExportRequest(kind="SUPERVISOR", view="FULL", locale="zh-CN"),
        idempotency_key="same-key",
    )
    with pytest.raises(IdempotencyConflictError):
        application.create(
            actor,
            report_id,
            ExportRequest(kind="SUPERVISOR", view="SUMMARY", locale="zh-CN"),
            idempotency_key="same-key",
        )


def test_package_sanitizes_zip_paths_and_records_missing_evidence_without_fake_files() -> None:
    package = assemble_report_package(
        pdfs={"../../项目负责人综合报告.pdf": b"supervisor", "用户报告.pdf": b"user"},
        source_directory=[{"title": "Source", "canonical_url": "https://example.invalid"}],
        evidence=[
            EvidenceArchiveEntry(
                evidence_id="e-1",
                filename="../../escape.txt",
                expected_sha256=hashlib.sha256(b"verified").hexdigest(),
                actual_sha256=hashlib.sha256(b"verified").hexdigest(),
                body=b"verified",
                missing_reason=None,
                citation_ids=("CIT-1",),
            ),
            EvidenceArchiveEntry(
                evidence_id="e-2",
                filename="missing.txt",
                expected_sha256="0" * 64,
                actual_sha256=None,
                body=None,
                missing_reason="OBJECT_NOT_FOUND",
                citation_ids=("CIT-2",),
            ),
        ],
        include_evidence=True,
    )
    with ZipFile(io.BytesIO(package)) as archive:
        names = archive.namelist()
        index = json.loads(archive.read("evidence-index.json"))

    assert all(".." not in name and not name.startswith(("/", "\\")) for name in names)
    assert "evidence/e-1/escape.txt" in names
    assert not any("e-2" in name and name != "evidence-index.json" for name in names)
    assert index["evidence"][1]["missing_reason"] == "OBJECT_NOT_FOUND"


def test_complete_package_contains_exactly_five_pdf_projections_and_the_source_manifests() -> None:
    application, sessions, objects, renderer, actor, report_id = _application()
    with sessions() as session:
        supervisor = session.execute(select(report).where(report.c.id == report_id)).mappings().one()
    run_id = supervisor["run_id"]
    agent_codes = ("user-evidence", "product-engineering", "business-investment", "evidence-auditor")
    now = datetime.now(UTC)
    with sessions.begin() as session:
        for agent_code in agent_codes:
            child_id = uuid4()
            document = {
                "schema_version": "2.0",
                "report_id": str(child_id),
                "run_id": str(run_id),
                "agent_code": agent_code,
                "product_title": "证据助手",
                "source_sha256": "c" * 64,
                "citations": [],
                "source_directory": [],
            }
            key = f"reports/{child_id}.json"
            digest = objects.put_private(key, json.dumps(document, sort_keys=True).encode(), "application/json")
            session.execute(
                agent_report_artifact.insert().values(
                    id=child_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=uuid4(),
                    agent_code=agent_code,
                    report_kind="DOMAIN",
                    revision=1,
                    object_key=key,
                    sha256=digest,
                    mime_type="application/json",
                    status="AVAILABLE",
                    created_at=now,
                )
            )

    result = application.create(
        actor,
        report_id,
        ExportRequest(kind="PACKAGE", view="FULL", locale="zh-CN"),
        idempotency_key="complete-package",
    )
    with ZipFile(io.BytesIO(objects.get_private(str(result.object_key)))) as archive:
        names = set(archive.namelist())

    assert len([name for name in names if name.endswith(".pdf")]) == 5
    assert {"项目负责人综合报告.pdf", "用户报告.pdf", "产品经理报告.pdf", "投资人报告.pdf", "证据校准报告.pdf"} <= names
    assert {"来源目录.html", "来源目录.json", "manifest.json"} <= names
    assert "evidence-index.json" not in names
    assert len(renderer.calls) == 5


class ApiExportApplication:
    def __init__(self, result: ExportResult) -> None:
        self.result = result
        self.calls: list[tuple[Actor, object, ExportRequest, str]] = []

    def create(self, actor, report_id, request, *, idempotency_key):
        self.calls.append((actor, report_id, request, idempotency_key))
        return self.result

    def get(self, _actor, _export_id):
        return self.result

    def read_url(self, _actor, _export_id):
        return {"export_id": str(self.result.export_id), "sha256": self.result.sha256, "size_bytes": 12, "read_url": "https://objects.invalid/export"}


class ShareResolver:
    def __init__(self, grant: PublicDemoShareGrant) -> None:
        self.grant = grant

    def resolve(self, _token: str) -> PublicDemoShareGrant:
        return self.grant


def _api_client(*, rate_limit: int = 20):
    tenant_id, run_id, report_id, export_id = (uuid4() for _ in range(4))
    result = ExportResult(
        export_id=export_id,
        report_id=report_id,
        run_id=run_id,
        kind="SUPERVISOR",
        agent_code=None,
        view="FULL",
        locale="zh-CN",
        include_evidence=False,
        source_sha256="a" * 64,
        status="COMPLETED",
        object_key="exports/report.pdf",
        sha256="b" * 64,
        size_bytes=12,
        error_code=None,
    )
    application = ApiExportApplication(result)
    grant = PublicDemoShareGrant(tenant_id, uuid4(), run_id, report_id, True, True)
    app = create_app(ControlPlane.create())
    app.state.report_export_application = application
    app.state.public_share_resolver = ShareResolver(grant)
    app.state.public_export_rate_limiter = PublicExportRateLimiter(limit=rate_limit, window_seconds=60)
    return TestClient(app), application, grant, result


def test_private_export_post_requires_write_headers_and_projects_the_durable_result() -> None:
    client, application, grant, result = _api_client()
    path = f"/api/v1/experience/reports/{result.report_id}/exports"
    body = {"kind": "SUPERVISOR", "view": "FULL", "locale": "zh-CN", "include_evidence": False}
    identity = {"X-Tenant-Id": str(grant.tenant_id), "X-Actor-Id": "owner"}

    assert client.post(path, json=body, headers=identity).status_code == 422
    response = client.post(
        path,
        json=body,
        headers={**identity, "Idempotency-Key": "api-export", "X-Correlation-Id": "corr-export"},
    )

    assert response.status_code == 201
    assert response.json()["export_id"] == str(result.export_id)
    assert application.calls[0][3] == "api-export"


def test_public_export_is_exact_report_scoped_and_rate_bounded() -> None:
    client, application, _grant, result = _api_client(rate_limit=1)
    token = "t" * 40
    headers = {"Idempotency-Key": "public-export", "X-Correlation-Id": "corr-public"}
    body = {"kind": "SUPERVISOR", "view": "FULL", "locale": "zh-CN", "include_evidence": False}

    wrong = client.post(
        f"/api/v1/public/demo/v2/reports/{uuid4()}/exports?token={token}",
        json=body,
        headers=headers,
    )
    limited = client.post(
        f"/api/v1/public/demo/v2/reports/{result.report_id}/exports?token={token}",
        json=body,
        headers=headers,
    )

    assert wrong.status_code == 404
    assert limited.status_code == 429
    assert application.calls == []
