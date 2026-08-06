"""Require private object, hash, and metadata verification before Evidence enters a Finding chain."""

from __future__ import annotations

from launchscope_api.infrastructure.object_store import ObjectStoreIntegrityError
from launchscope_api.modules.project_dossier.material_ingestion import QuarantineObjectStore
from launchscope_domain.aggregates.evidence_review import Evidence, EvidenceReview


class EvidenceCaptureError(ValueError):
    """The object cannot safely be recorded as an immutable Evidence fact."""


class EvidenceCaptureApplication:
    """Adds evidence only after a private S3 HEAD agrees with its declared ref."""

    def __init__(self, object_store: QuarantineObjectStore) -> None:
        self.object_store = object_store

    def capture(self, review: EvidenceReview, evidence: Evidence) -> Evidence:
        scope = review.scope
        if evidence.scope != scope:
            raise EvidenceCaptureError("evidence must use the same fully-qualified Run scope as its review")
        expected_prefix = (
            f"tenant/{scope.tenant_id}/project/{scope.project_id}/version/{scope.product_version_id}/"
            f"run/{scope.run_id}/evidence/{evidence.evidence_id}"
        )
        if not evidence.ref.object_key.startswith(expected_prefix):
            raise EvidenceCaptureError("evidence object key is outside its tenant/project/version/run evidence path")
        if evidence.ref.size_bytes <= 0:
            raise EvidenceCaptureError("evidence must declare a positive object size")
        try:
            observed = self.object_store.head(evidence.ref.object_key)
        except ObjectStoreIntegrityError as exc:
            raise EvidenceCaptureError("evidence object metadata is incomplete") from exc
        if observed is None:
            raise EvidenceCaptureError("evidence object was not found in the private object store")
        if (
            observed.sha256 != evidence.ref.sha256
            or observed.size_bytes != evidence.ref.size_bytes
            or observed.mime_type != evidence.ref.mime_type
        ):
            raise EvidenceCaptureError("evidence object HEAD metadata does not match its immutable evidence reference")
        return review.add_evidence(evidence)


__all__ = ["EvidenceCaptureApplication", "EvidenceCaptureError"]
