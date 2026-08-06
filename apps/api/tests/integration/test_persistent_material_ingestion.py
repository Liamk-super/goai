"""PostgreSQL RLS acceptance for the durable direct-upload completion path."""

from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select

from launchscope_api.infrastructure.db.schema import material
from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError
from launchscope_api.modules.project_dossier.material_ingestion import (
    ObjectMetadata,
    PersistentMaterialIngestionApplication,
    QuarantineObjectStore,
)


class VerifiedObjectStore(QuarantineObjectStore):
    def __init__(self, metadata: ObjectMetadata) -> None:
        self.metadata = metadata
        self.requested: list[tuple[str, str]] = []

    def initiate_upload(self, object_key: str, mime_type: str, size_bytes: int, *, sha256: str | None = None) -> str:
        assert sha256 == self.metadata.sha256
        self.requested.append((object_key, mime_type))
        return f"https://private-object-store.invalid/{object_key}"

    def head(self, object_key: str) -> ObjectMetadata | None:
        return self.metadata


def test_persistent_completion_uses_rls_and_persists_verified_object_metadata(
    database, runtime_engine, tenant_records
) -> None:
    scope = tenant_records["scope"]
    content = b"private evidence"
    digest = sha256(content).hexdigest()
    store = VerifiedObjectStore(ObjectMetadata(digest, len(content), "text/plain", etag="server-etag"))
    service = PersistentMaterialIngestionApplication(session_factory(runtime_engine), store)
    actor = Actor(scope.tenant_id, "alice")

    upload = service.initiate(
        actor,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        product_version_id=scope.product_version_id,
        display_name="brief.txt",
        sha256=digest,
        size_bytes=len(content),
        mime_type="text/plain",
    )
    completed = service.complete(actor, upload.material_id)

    assert completed.status.value == "VALIDATED"
    assert store.requested == [(upload.object_key, "text/plain")]
    with database.connect() as connection:
        row = connection.execute(select(material).where(material.c.id == upload.material_id)).mappings().one()
    assert row["ingest_status"] == "VALIDATED"
    assert row["object_metadata"] == {
        "sha256": digest,
        "size_bytes": str(len(content)),
        "mime_type": "text/plain",
        "etag": "server-etag",
    }


def test_persistent_completion_cannot_cross_tenant_rls_scope(database, runtime_engine, tenant_records) -> None:
    scope = tenant_records["scope"]
    digest = sha256(b"scope").hexdigest()
    service = PersistentMaterialIngestionApplication(
        session_factory(runtime_engine), VerifiedObjectStore(ObjectMetadata(digest, 5, "text/plain"))
    )
    upload = service.initiate(
        Actor(scope.tenant_id, "alice"),
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        product_version_id=scope.product_version_id,
        display_name="brief.txt",
        sha256=digest,
        size_bytes=5,
        mime_type="text/plain",
    )
    with pytest.raises(AuthorizationError):
        service.complete(Actor(uuid4(), "bob"), upload.material_id)
