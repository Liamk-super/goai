"""T10 durable read-model evidence; no in-memory control-plane state is used."""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from launchscope_api.infrastructure.db.schema import workspace_member
from launchscope_api.infrastructure.db.session import create_database_engine, session_factory
from launchscope_api.main import PersistentControlPlane, create_app
from launchscope_api.modules.experience.read_model import ExperienceReadApplication
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.material_ingestion import InMemoryQuarantineObjectStore, ObjectMetadata
from launchscope_api.modules.project_dossier.persistent_application import (
    PersistentIdentityTenantApplication,
    PersistentProjectDossierApplication,
)


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
    answers = {
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
    assert (
        client.post(
            f"/api/v1/product-versions/{version_id}/profile-confirmations",
            headers=headers,
            json={"acknowledge_model_inference": True},
        ).status_code
        == 201
    )
    planned = client.post(f"/api/v1/product-versions/{version_id}/plan", headers=headers)
    assert planned.status_code == 200, planned.text
    run_id = planned.json()["run_id"]

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
