from __future__ import annotations

from hashlib import sha256
from uuid import uuid4

import pytest

from launchscope_api.modules.evidence.application import EvidenceCaptureApplication, EvidenceCaptureError
from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata, QuarantineObjectStore
from launchscope_domain import Evidence, EvidenceReview, TenantScope


class MetadataObjectStore(QuarantineObjectStore):
    def __init__(self, metadata: ObjectMetadata) -> None:
        self.metadata = metadata

    def initiate_upload(self, object_key: str, mime_type: str, size_bytes: int, *, sha256: str | None = None) -> str:
        raise AssertionError("capture must not create uploads")

    def head(self, object_key: str) -> ObjectMetadata | None:
        return self.metadata


def test_evidence_capture_requires_private_object_metadata() -> None:
    content = b"captured evidence"
    digest = sha256(content).hexdigest()
    scope = TenantScope(uuid4(), workspace_id=uuid4(), project_id=uuid4(), product_version_id=uuid4(), run_id=uuid4())
    evidence = Evidence.create(
        scope,
        object_key=(
            f"tenant/{scope.tenant_id}/project/{scope.project_id}/version/{scope.product_version_id}/"
            f"run/{scope.run_id}/evidence/00000000-0000-4000-8000-000000000010/source.txt"
        ),
        evidence_id="00000000-0000-4000-8000-000000000010",
        sha256=digest,
        size_bytes=len(content),
        mime_type="text/plain",
        source_type="MATERIAL",
        trust_level="E3",
    )
    review = EvidenceReview(scope)
    capture = EvidenceCaptureApplication(MetadataObjectStore(ObjectMetadata(digest, len(content), "text/plain")))
    captured = capture.capture(review, evidence)
    assert captured.evidence_id in review.evidence

    with pytest.raises(EvidenceCaptureError, match="does not match"):
        EvidenceCaptureApplication(MetadataObjectStore(ObjectMetadata("0" * 64, len(content), "text/plain"))).capture(
            EvidenceReview(scope), evidence
        )
