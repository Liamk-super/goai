from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

from launchscope_api.modules.project_dossier.material_ingestion import InMemoryQuarantineObjectStore, ObjectMetadata

from .conftest import client_and_plane, create_tenant, headers


def test_unconfirmed_profile_cannot_create_a_planned_run_or_downstream_work() -> None:
    client, plane = client_and_plane()
    tenant_id, workspace_id = create_tenant(client, "alice", "no-plan")
    correlation_id = str(uuid4())
    request_headers = headers(tenant_id, "alice", correlation_id)
    project_id = client.post(
        "/api/v1/projects", headers=request_headers, json={"workspace_id": workspace_id, "name": "Product"}
    ).json()["project_id"]
    version_id = client.post(
        f"/api/v1/projects/{project_id}/versions", headers=request_headers, json={"label": "V1"}
    ).json()["product_version_id"]
    content = b"verified"
    digest = sha256(content).hexdigest()
    material = client.post(
        f"/api/v1/product-versions/{version_id}/materials:initiate",
        headers=request_headers,
        json={"display_name": "brief.txt", "sha256": digest, "size_bytes": len(content), "mime_type": "text/plain"},
    ).json()
    store = plane.dossier.materials.object_store
    assert isinstance(store, InMemoryQuarantineObjectStore)
    store.stage_uploaded_object(material["object_key"], ObjectMetadata(digest, len(content), "text/plain"))
    assert (
        client.post(f"/api/v1/materials/{material['material_id']}/complete", headers=request_headers).status_code == 200
    )
    assert (
        client.post(f"/api/v1/product-versions/{version_id}/gap-questions", headers=request_headers).status_code == 200
    )

    rejected = client.post(f"/api/v1/product-versions/{version_id}/plan", headers=request_headers)
    assert rejected.status_code == 422
    assert rejected.json()["error_code"] == "PRECONDITION_FAILED"
    assert plane.dossier.intake.runs == {}
    # T5 has no budget reservation or worker-dispatch call, and its gate leaves
    # no EvaluationRun for those later modules to consume.
