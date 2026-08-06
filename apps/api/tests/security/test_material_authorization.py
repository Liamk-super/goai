from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid4

from launchscope_api.modules.identity_tenant.application import Actor, WorkspaceRole
from launchscope_api.modules.project_dossier.material_ingestion import InMemoryQuarantineObjectStore, ObjectMetadata

from ..api.conftest import client_and_plane, create_tenant, headers


def test_other_tenant_cannot_complete_or_access_same_named_material() -> None:
    client, plane = client_and_plane()
    correlation_id = str(uuid4())
    tenant_a, workspace_a = create_tenant(client, "alice", "tenant-a")
    tenant_b, _ = create_tenant(client, "bob", "tenant-b")
    alice_headers = headers(tenant_a, "alice", correlation_id)
    bob_headers = headers(tenant_b, "bob", correlation_id)
    project_id = client.post(
        "/api/v1/projects", headers=alice_headers, json={"workspace_id": workspace_a, "name": "Same Name"}
    ).json()["project_id"]
    version_id = client.post(
        f"/api/v1/projects/{project_id}/versions", headers=alice_headers, json={"label": "V1"}
    ).json()["product_version_id"]
    content = b"same-name.txt"
    digest = sha256(content).hexdigest()
    material = client.post(
        f"/api/v1/product-versions/{version_id}/materials:initiate",
        headers=alice_headers,
        json={"display_name": "same-name.txt", "sha256": digest, "size_bytes": len(content), "mime_type": "text/plain"},
    ).json()
    assert f"tenant/{tenant_a}/" in material["object_key"]
    assert f"tenant/{tenant_b}/" not in material["object_key"]

    forbidden = client.post(f"/api/v1/materials/{material['material_id']}/complete", headers=bob_headers)
    assert forbidden.status_code == 403
    store = plane.dossier.materials.object_store
    assert isinstance(store, InMemoryQuarantineObjectStore)
    store.stage_uploaded_object(material["object_key"], ObjectMetadata(digest, len(content), "text/plain"))
    assert (
        client.post(f"/api/v1/materials/{material['material_id']}/complete", headers=alice_headers).status_code == 200
    )


def test_same_tenant_viewer_cannot_mutate_material_before_authorization() -> None:
    client, plane = client_and_plane()
    correlation_id = str(uuid4())
    tenant_id, workspace_id = create_tenant(client, "alice", "viewer-boundary")
    alice_headers = headers(tenant_id, "alice", correlation_id)
    viewer_headers = headers(tenant_id, "viewer", correlation_id)
    plane.identity.add_member(
        Actor(UUID(tenant_id), "alice"),
        UUID(workspace_id),
        "viewer",
        WorkspaceRole.VIEWER,
    )
    project_id = client.post(
        "/api/v1/projects", headers=alice_headers, json={"workspace_id": workspace_id, "name": "Protected"}
    ).json()["project_id"]
    version_id = client.post(
        f"/api/v1/projects/{project_id}/versions", headers=alice_headers, json={"label": "V1"}
    ).json()["product_version_id"]
    content = b"viewer cannot complete"
    digest = sha256(content).hexdigest()
    material = client.post(
        f"/api/v1/product-versions/{version_id}/materials:initiate",
        headers=alice_headers,
        json={"display_name": "brief.txt", "sha256": digest, "size_bytes": len(content), "mime_type": "text/plain"},
    ).json()
    store = plane.dossier.materials.object_store
    assert isinstance(store, InMemoryQuarantineObjectStore)
    store.stage_uploaded_object(material["object_key"], ObjectMetadata(digest, len(content), "text/plain"))

    forbidden = client.post(f"/api/v1/materials/{material['material_id']}/complete", headers=viewer_headers)
    assert forbidden.status_code == 403
    assert plane.dossier.materials.materials[UUID(material["material_id"])].status.value == "UPLOADING"
