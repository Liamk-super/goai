from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from launchscope_api.modules.project_dossier.material_ingestion import InMemoryQuarantineObjectStore, ObjectMetadata

from .conftest import client_and_plane, create_tenant, headers


def test_project_portrait_endpoint_returns_the_confirmed_profile_projection() -> None:
    from fastapi.testclient import TestClient

    from launchscope_api.main import ControlPlane, create_app
    from launchscope_api.modules.experience.api import get_read_model

    project_id = uuid4()
    version_id = uuid4()
    read_model = SimpleNamespace(
        project_portrait=lambda actor, requested_project_id: {
            "project_id": str(requested_project_id),
            "product_version_id": str(version_id),
            "version_label": "V1",
            "version_number": 1,
            "confirmed_at": "2026-08-16T00:00:00Z",
            "confirmed_fields": {"stage": "只有想法", "target_user": "研究生"},
        }
    )
    app = create_app(ControlPlane.create())
    app.dependency_overrides[get_read_model] = lambda: read_model

    response = TestClient(app).get(
        f"/api/v1/projects/{project_id}/portrait",
        headers={"X-Tenant-Id": str(uuid4()), "X-Actor-Id": "alice", "X-Correlation-Id": str(uuid4())},
    )

    assert response.status_code == 200, response.text
    assert response.json()["product_version_id"] == str(version_id)
    assert response.json()["confirmed_fields"]["stage"] == "只有想法"


def test_cors_is_opt_in_and_allows_only_configured_local_workspace_origin(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from launchscope_api.main import ControlPlane, create_app

    monkeypatch.setenv("LAUNCHSCOPE_CORS_ORIGINS", "http://127.0.0.1:13000")
    client = TestClient(create_app(ControlPlane.create()))
    allowed = client.options(
        "/api/v1/projects",
        headers={"Origin": "http://127.0.0.1:13000", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:13000"
    blocked = client.options(
        "/api/v1/projects",
        headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in blocked.headers


def test_upload_to_confirmed_profile_to_planned_api_chain() -> None:
    client, plane = client_and_plane()
    tenant_id, workspace_id = create_tenant(client, "alice", "acme")
    correlation_id = str(uuid4())
    request_headers = headers(tenant_id, "alice", correlation_id)

    project = client.post(
        "/api/v1/projects", headers=request_headers, json={"workspace_id": workspace_id, "name": "Widget"}
    )
    assert project.status_code == 201, project.text
    version = client.post(
        f"/api/v1/projects/{project.json()['project_id']}/versions", headers=request_headers, json={"label": "V1"}
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["product_version_id"]

    content = b"launchscope material"
    digest = sha256(content).hexdigest()
    upload = client.post(
        f"/api/v1/product-versions/{version_id}/materials:initiate",
        headers=request_headers,
        json={"display_name": "brief.txt", "sha256": digest, "size_bytes": len(content), "mime_type": "text/plain"},
    )
    assert upload.status_code == 201, upload.text
    upload_payload = upload.json()
    assert f"tenant/{tenant_id}/workspace/{workspace_id}/" in upload_payload["object_key"]
    assert "/quarantine/" in upload_payload["object_key"]

    store = plane.dossier.materials.object_store
    assert isinstance(store, InMemoryQuarantineObjectStore)
    store.stage_uploaded_object(upload_payload["object_key"], ObjectMetadata(digest, len(content), "text/plain"))
    completed = client.post(f"/api/v1/materials/{upload_payload['material_id']}/complete", headers=request_headers)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "VALIDATED"

    gaps = client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=request_headers)
    assert gaps.status_code == 200, gaps.text
    gap_payload = gaps.json()
    assert gap_payload["correlation_id"] == correlation_id
    assert gap_payload["profile_draft"]["source"] == "MODEL_INFERENCE"
    assert gap_payload["profile_draft"]["user_confirmed_fields"] == {}
    assert 3 <= len(gap_payload["questions"]) <= 6
    assert [question["priority"] for question in gap_payload["questions"]] == list(
        range(1, len(gap_payload["questions"]) + 1)
    )

    answers = {
        "one_line_value_claim": "Help independent retailers find inventory risks before weekly ordering",
        "target_user": "Independent retailers",
        "payer": "Store owner",
        "stage": "Private beta",
        "region": "Hong Kong",
        "validation_goal": "Decide whether to fund pilot onboarding",
    }
    answered = client.post(
        f"/api/v1/product-versions/{version_id}/gap-answers",
        headers=request_headers,
        json={"correlation_id": correlation_id, "answers": answers},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["profile_draft"]["user_confirmed_fields"] == {}

    confirmation = client.post(
        f"/api/v1/product-versions/{version_id}/profile-confirmations",
        headers=request_headers,
        json={"acknowledge_model_inference": True},
    )
    assert confirmation.status_code == 201, confirmation.text
    assert confirmation.json()["confirmed_fields"] == answers

    resumed_gaps = client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=request_headers)
    assert resumed_gaps.status_code == 200, resumed_gaps.text
    assert resumed_gaps.json()["questions"] == []
    assert resumed_gaps.json()["profile_draft"]["status"] == "CONFIRMED"
    assert resumed_gaps.json()["profile_draft"]["user_confirmed_fields"] == answers

    repeated_confirmation = client.post(
        f"/api/v1/product-versions/{version_id}/profile-confirmations",
        headers=request_headers,
        json={"acknowledge_model_inference": True},
    )
    assert repeated_confirmation.status_code == 201, repeated_confirmation.text
    assert repeated_confirmation.json()["profile_id"] == confirmation.json()["profile_id"]

    planned = client.post(f"/api/v1/product-versions/{version_id}/plan", headers=request_headers)
    assert planned.status_code == 200, planned.text
    assert planned.json()["status"] == "PLANNED"
    assert planned.json()["correlation_id"] == correlation_id
