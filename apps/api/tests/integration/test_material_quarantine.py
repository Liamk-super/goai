from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest

from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.material_ingestion import (
    InMemoryQuarantineObjectStore,
    MaterialIngestionApplication,
    MaterialStatus,
    ObjectMetadata,
)


@pytest.mark.parametrize(
    ("observed_sha256", "observed_size", "observed_mime"),
    [
        (sha256(b"wrong").hexdigest(), 8, "text/plain"),
        (sha256(b"expected").hexdigest(), 7, "text/plain"),
        (sha256(b"expected").hexdigest(), 8, "application/pdf"),
    ],
    ids=["hash", "size", "mime"],
)
def test_completion_rejects_object_metadata_mismatches_after_quarantine(
    observed_sha256: str,
    observed_size: int,
    observed_mime: str,
) -> None:
    actor = Actor(uuid4(), "alice")
    store = InMemoryQuarantineObjectStore()
    ingestion = MaterialIngestionApplication(store)
    expected = sha256(b"expected").hexdigest()
    upload = ingestion.initiate(
        actor,
        workspace_id=uuid4(),
        project_id=uuid4(),
        product_version_id=uuid4(),
        display_name="brief.txt",
        sha256=expected,
        size_bytes=8,
        mime_type="text/plain",
    )
    store.stage_uploaded_object(upload.object_key, ObjectMetadata(observed_sha256, observed_size, observed_mime))

    result = ingestion.complete(actor, upload.material_id)
    assert result.status is MaterialStatus.REJECTED
    assert result.rejection_reason == "object metadata does not match the upload initiation"
