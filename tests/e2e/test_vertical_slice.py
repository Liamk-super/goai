"""T12 deterministic local E2E over the real HTTP API and PostgreSQL facts."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from launchscope_api.infrastructure.db.schema import evidence_audit, matrix_handoff, outbox_message, task
from launchscope_api.infrastructure.db.session import create_database_engine, session_factory
from launchscope_api.main import PersistentControlPlane, create_app
from launchscope_api.modules.evaluation.vertical_slice_application import VerticalSliceApplication
from launchscope_api.modules.experience.read_model import ExperienceReadApplication
from launchscope_api.modules.project_dossier.material_ingestion import InMemoryQuarantineObjectStore, ObjectMetadata
from launchscope_api.modules.project_dossier.persistent_application import (
    PersistentIdentityTenantApplication,
    PersistentProjectDossierApplication,
)

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


class RecordingPrivateObjects:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str:
        self.objects[object_key] = (payload, mime_type)
        return sha256(payload).hexdigest()


def _runtime(database, runtime_engine, monkeypatch):
    sessions = session_factory(runtime_engine)
    identity = PersistentIdentityTenantApplication(sessions)
    upload_store = InMemoryQuarantineObjectStore()
    plane = PersistentControlPlane(
        identity=identity,
        dossier=PersistentProjectDossierApplication(sessions, identity, upload_store),
    )
    ops_engine = create_database_engine(
        database.url.render_as_string(hide_password=False), application_role="launchscope_ops"
    )
    app = create_app(plane)
    objects = RecordingPrivateObjects()
    app.state.vertical_slice = VerticalSliceApplication(sessions, objects, FIXTURE_ROOT)
    app.state.experience_read_model = ExperienceReadApplication(sessions, ops_sessions=session_factory(ops_engine))
    monkeypatch.setenv("LAUNCHSCOPE_OPS_AUDIT_ACTORS", "ops-auditor")
    return TestClient(app), upload_store, objects


def _create_planned_run(
    client: TestClient,
    upload_store: InMemoryQuarantineObjectStore,
    version: str,
    *,
    headers: dict[str, str] | None = None,
    project_id: str | None = None,
):
    if headers is None or project_id is None:
        tenant = client.post(
            "/api/v1/tenants",
            headers={"X-Actor-Id": "demo-owner"},
            json={"slug": f"e2e-{uuid4()}", "workspace_name": "Demo workspace"},
        ).json()
        headers = {
            "X-Tenant-Id": tenant["tenant_id"],
            "X-Actor-Id": "demo-owner",
            "X-Correlation-Id": str(uuid4()),
        }
        project = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"workspace_id": tenant["workspace_id"], "name": "Evidence console"},
        ).json()
        project_id = project["project_id"]
    created_version = client.post(
        f"/api/v1/projects/{project_id}/versions",
        headers=headers,
        json={"label": version.upper()},
    ).json()
    version_id = created_version["product_version_id"]
    fixture = FIXTURE_ROOT / version / "product-materials" / "brief.md"
    content = fixture.read_bytes()
    digest = sha256(content).hexdigest()
    initiated = client.post(
        f"/api/v1/product-versions/{version_id}/materials:initiate",
        headers=headers,
        json={
            "display_name": "brief.md",
            "sha256": digest,
            "size_bytes": len(content),
            "mime_type": "text/plain",
        },
    ).json()
    upload_store.stage_uploaded_object(initiated["object_key"], ObjectMetadata(digest, len(content), "text/plain"))
    assert client.post(f"/api/v1/materials/{initiated['material_id']}/complete", headers=headers).status_code == 200
    gaps = client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=headers).json()
    answers = {
        "one_line_value_claim": "Validate evidence-backed product decisions before committing more resources",
        "target_user": "Small product teams",
        "payer": "Team owner",
        "stage": "Local demo",
        "region": "Hong Kong",
        "validation_goal": "Validate the evidence-driven local workflow",
    }
    assert (
        client.post(
            f"/api/v1/product-versions/{version_id}/gap-answers",
            headers=headers,
            json={"correlation_id": gaps["correlation_id"], "answers": answers},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/product-versions/{version_id}/profile-confirmations",
            headers=headers,
            json={"acknowledge_model_inference": True},
        ).status_code
        == 201
    )
    planned = client.post(f"/api/v1/product-versions/{version_id}/plan", headers=headers).json()
    return headers, project_id, planned["run_id"]


def test_local_vertical_slice_persists_tool_handoffs_audit_report_sse_and_ops(
    database, runtime_engine, monkeypatch
) -> None:
    client, upload_store, objects = _runtime(database, runtime_engine, monkeypatch)
    headers, _project_id, run_id = _create_planned_run(client, upload_store, "v1")
    executed = client.post(
        f"/api/v1/runs/{run_id}/execute-local-demo",
        headers=headers,
        json={"fixture_path": "v1/product-materials/brief.md"},
    )
    assert executed.status_code == 200, executed.text
    payload = executed.json()
    assert payload["status"] == "COMPLETED"
    assert payload["execution_mode"] == "LOCAL_DETERMINISTIC_READONLY"
    assert payload["handoff_count"] == 4
    assert payload["tool_invocation_count"] == 1
    assert len(objects.objects) == 2

    report_response = client.get(f"/api/v1/experience/reports/{payload['report_id']}", headers=headers)
    assert report_response.status_code == 200, report_response.text
    assert report_response.json()["evidence_chain"]
    stream = client.get(f"/api/v1/runs/{run_id}/events?cursor=event.initial", headers=headers)
    assert stream.status_code == 200
    assert "COMPLETED" in stream.text
    ops = client.get("/api/v1/ops/audit/events", headers={"X-Ops-Actor-Id": "ops-auditor"})
    assert ops.status_code == 200
    assert all("payload" not in item for item in ops.json()["items"])

    tenant_id = headers["X-Tenant-Id"]
    with database.connect() as connection:
        assert (
            connection.execute(
                select(func.count()).select_from(matrix_handoff).where(matrix_handoff.c.tenant_id == tenant_id)
            ).scalar_one()
            == 4
        )
        assert (
            connection.execute(select(func.count()).select_from(task).where(task.c.tenant_id == tenant_id)).scalar_one()
            == 5
        )
        assert "DOWNGRADED" in set(
            connection.execute(
                select(evidence_audit.c.decision).where(evidence_audit.c.tenant_id == tenant_id)
            ).scalars()
        )
        assert (
            connection.execute(
                select(func.count())
                .select_from(outbox_message)
                .where(
                    outbox_message.c.tenant_id == tenant_id,
                    outbox_message.c.event_type == "run.status_changed",
                )
            ).scalar_one()
            == 4
        )

    repeated = client.post(
        f"/api/v1/runs/{run_id}/execute-local-demo",
        headers=headers,
        json={"fixture_path": "v1/product-materials/brief.md"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["report_id"] == payload["report_id"]


def test_v1_v2_use_same_project_standard_and_durable_comparison(database, runtime_engine, monkeypatch) -> None:
    client, upload_store, _objects = _runtime(database, runtime_engine, monkeypatch)
    headers, project_id, v1_run = _create_planned_run(client, upload_store, "v1")
    assert (
        client.post(
            f"/api/v1/runs/{v1_run}/execute-local-demo",
            headers=headers,
            json={"fixture_path": "v1/product-materials/brief.md"},
        ).status_code
        == 200
    )
    headers, same_project_id, v2_run = _create_planned_run(
        client,
        upload_store,
        "v2",
        headers=headers,
        project_id=project_id,
    )
    assert same_project_id == project_id
    assert (
        client.post(
            f"/api/v1/runs/{v2_run}/execute-local-demo",
            headers=headers,
            json={"fixture_path": "v2/product-materials/brief.md"},
        ).status_code
        == 200
    )
    comparison = client.get(f"/api/v1/experience/projects/{project_id}/compare/{v2_run}", headers=headers)
    assert comparison.status_code == 200, comparison.text
    assert comparison.json() == {
        "project_id": project_id,
        "baseline_run_id": v1_run,
        "candidate_run_id": v2_run,
        "comparable": True,
        "standard_version": "1.0",
        "supplemental_standard_version": None,
            "baseline_status": "COMPLETED",
            "candidate_status": "COMPLETED",
            "dimension_changes": {
                "PRODUCT_IMPLEMENTATION": "UNCHANGED",
                "USER_USAGE": "UNCHANGED",
                "BUSINESS_INVESTMENT": "UNCHANGED",
                "GEO_POLICY_TREND": "UNCHANGED",
            },
            "new_risks": [],
        }
