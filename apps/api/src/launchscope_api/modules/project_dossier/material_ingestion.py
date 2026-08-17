"""Quarantined material ingestion with object-store verified completion facts."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_MIME_TYPES = frozenset(
    {
        "application/json",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.launchscope.material-analysis+json",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/markdown",
        "text/plain",
    }
)
MAX_MATERIAL_BYTES = 20 * 1024 * 1024


class MaterialStatus(StrEnum):
    UPLOADING = "UPLOADING"
    QUARANTINED = "QUARANTINED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class MaterialValidationError(ValueError):
    """A file cannot enter the product dossier material set."""


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    sha256: str
    size_bytes: int
    mime_type: str
    etag: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UploadInitiation:
    material_id: UUID
    object_key: str
    upload_url: str


@dataclass
class MaterialRecord:
    material_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    project_id: UUID
    product_version_id: UUID
    object_key: str
    display_name: str
    expected_sha256: str
    expected_size_bytes: int
    expected_mime_type: str
    status: MaterialStatus = MaterialStatus.UPLOADING
    rejection_reason: str | None = None


class QuarantineObjectStore:
    """Object-storage port.  Completion must inspect this store, not the client."""

    def initiate_upload(
        self, object_key: str, mime_type: str, size_bytes: int, *, sha256: str | None = None
    ) -> str:
        raise NotImplementedError

    def head(self, object_key: str) -> ObjectMetadata | None:
        raise NotImplementedError


@dataclass
class InMemoryQuarantineObjectStore(QuarantineObjectStore):
    """Executable local adapter; tests stage object metadata as a direct-upload callback would."""

    objects: dict[str, ObjectMetadata] = field(default_factory=dict)

    def initiate_upload(
        self, object_key: str, mime_type: str, size_bytes: int, *, sha256: str | None = None
    ) -> str:
        return f"memory://quarantine/{object_key}?content-type={mime_type}&content-length={size_bytes}"

    def stage_uploaded_object(self, object_key: str, metadata: ObjectMetadata) -> None:
        self.objects[object_key] = metadata

    def head(self, object_key: str) -> ObjectMetadata | None:
        return self.objects.get(object_key)


class MaterialIngestionApplication:
    """Creates only tenant/version-scoped quarantine keys and validates callback facts."""

    def __init__(self, object_store: QuarantineObjectStore | None = None) -> None:
        self.object_store = object_store or InMemoryQuarantineObjectStore()
        self.materials: dict[UUID, MaterialRecord] = {}

    def initiate(
        self,
        actor: Actor,
        *,
        workspace_id: UUID,
        project_id: UUID,
        product_version_id: UUID,
        display_name: str,
        sha256: str,
        size_bytes: int,
        mime_type: str,
    ) -> UploadInitiation:
        normalized_sha = _sha256(sha256)
        _validate_metadata(size_bytes, mime_type)
        if display_name.strip().lower().endswith(".doc"):
            raise MaterialValidationError("UNSUPPORTED_LEGACY_DOC: convert the file to DOCX before uploading")
        if not display_name.strip() or len(display_name.strip()) > 255:
            raise MaterialValidationError("display_name must be a non-empty string up to 255 characters")
        material_id = uuid4()
        object_key = (
            f"tenant/{actor.tenant_id}/workspace/{workspace_id}/project/{project_id}/"
            f"version/{product_version_id}/quarantine/{material_id}"
        )
        record = MaterialRecord(
            material_id=material_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            product_version_id=product_version_id,
            object_key=object_key,
            display_name=display_name.strip(),
            expected_sha256=normalized_sha,
            expected_size_bytes=size_bytes,
            expected_mime_type=mime_type,
        )
        self.materials[material_id] = record
        upload_url = self.object_store.initiate_upload(object_key, mime_type, size_bytes, sha256=normalized_sha)
        return UploadInitiation(material_id, object_key, upload_url)

    def complete(self, actor: Actor, material_id: UUID) -> MaterialRecord:
        record = self._owned_record(actor, material_id)
        if record.status is not MaterialStatus.UPLOADING:
            raise MaterialValidationError("material completion callback is only accepted once")
        record.status = MaterialStatus.QUARANTINED
        observed = self.object_store.head(record.object_key)
        if observed is None:
            return self._reject(record, "object_not_found")
        try:
            _validate_metadata(observed.size_bytes, observed.mime_type)
        except MaterialValidationError as exc:
            return self._reject(record, str(exc))
        if (
            _sha256(observed.sha256) != record.expected_sha256
            or observed.size_bytes != record.expected_size_bytes
            or observed.mime_type != record.expected_mime_type
        ):
            return self._reject(record, "object metadata does not match the upload initiation")
        record.status = MaterialStatus.VALIDATED
        return record

    def list_validated(self, actor: Actor, product_version_id: UUID) -> tuple[MaterialRecord, ...]:
        return tuple(
            item
            for item in self.materials.values()
            if item.tenant_id == actor.tenant_id
            and item.product_version_id == product_version_id
            and item.status is MaterialStatus.VALIDATED
        )

    def _owned_record(self, actor: Actor, material_id: UUID) -> MaterialRecord:
        record = self.materials.get(material_id)
        if record is None or record.tenant_id != actor.tenant_id:
            raise AuthorizationError("material is outside the caller tenant")
        return record

    @staticmethod
    def _reject(record: MaterialRecord, reason: str) -> MaterialRecord:
        record.status = MaterialStatus.REJECTED
        record.rejection_reason = reason
        return record


class PersistentMaterialIngestionApplication:
    """T6 material completion service using PostgreSQL plus a real object store.

    The application reads HEAD facts after a direct upload and performs one
    compare-and-set terminal transition.  A browser callback is therefore never
    trusted as upload proof and retries cannot turn a rejected record valid.
    """

    def __init__(self, session_factory: sessionmaker[Session], object_store: QuarantineObjectStore) -> None:
        self._session_factory = session_factory
        self.object_store = object_store

    def initiate(
        self,
        actor: Actor,
        *,
        workspace_id: UUID,
        project_id: UUID,
        product_version_id: UUID,
        display_name: str,
        sha256: str,
        size_bytes: int,
        mime_type: str,
    ) -> UploadInitiation:
        normalized_sha = _sha256(sha256)
        _validate_metadata(size_bytes, mime_type)
        if display_name.strip().lower().endswith(".doc"):
            raise MaterialValidationError("UNSUPPORTED_LEGACY_DOC: convert the file to DOCX before uploading")
        if not display_name.strip() or len(display_name.strip()) > 255:
            raise MaterialValidationError("display_name must be a non-empty string up to 255 characters")
        material_id = uuid4()
        object_key = (
            f"tenant/{actor.tenant_id}/workspace/{workspace_id}/project/{project_id}/"
            f"version/{product_version_id}/quarantine/{material_id}"
        )
        record = MaterialRecord(
            material_id=material_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            product_version_id=product_version_id,
            object_key=object_key,
            display_name=display_name.strip(),
            expected_sha256=normalized_sha,
            expected_size_bytes=size_bytes,
            expected_mime_type=mime_type,
        )
        upload_url = self.object_store.initiate_upload(object_key, mime_type, size_bytes, sha256=normalized_sha)
        self._with_repository(record, lambda repository: repository.create(record))
        return UploadInitiation(material_id, object_key, upload_url)

    def complete(self, actor: Actor, material_id: UUID) -> MaterialRecord:
        from launchscope_api.infrastructure.db.session import tenant_transaction
        from launchscope_api.infrastructure.repositories.material_ingestion import SqlAlchemyMaterialIngestionRepository
        from launchscope_domain.value_objects import TenantScope

        with tenant_transaction(self._session_factory, TenantScope(actor.tenant_id)) as session:
            repository = SqlAlchemyMaterialIngestionRepository(session)
            record = repository.get(material_id, actor.tenant_id)
            if record is None:
                raise AuthorizationError("material is outside the caller tenant")
            if record.status is not MaterialStatus.UPLOADING:
                raise MaterialValidationError("material completion callback is only accepted once")
            observed = self.object_store.head(record.object_key)
            metadata: dict[str, str] = {}
            if observed is None:
                result = MaterialIngestionApplication._reject(record, "object_not_found")
            else:
                metadata = {
                    "sha256": observed.sha256,
                    "size_bytes": str(observed.size_bytes),
                    "mime_type": observed.mime_type,
                    "etag": observed.etag,
                    **dict(observed.metadata),
                }
                try:
                    _validate_metadata(observed.size_bytes, observed.mime_type)
                except MaterialValidationError as exc:
                    result = MaterialIngestionApplication._reject(record, str(exc))
                else:
                    result = record
                    if (
                        _sha256(observed.sha256) != record.expected_sha256
                        or observed.size_bytes != record.expected_size_bytes
                        or observed.mime_type != record.expected_mime_type
                    ):
                        result = MaterialIngestionApplication._reject(
                            record, "object metadata does not match the upload initiation"
                        )
                    else:
                        result.status = MaterialStatus.VALIDATED
            if not repository.complete(result, metadata=metadata):
                raise MaterialValidationError("material completion callback is only accepted once")
            return result

    def list_validated(self, actor: Actor, product_version_id: UUID) -> tuple[MaterialRecord, ...]:
        from launchscope_api.infrastructure.db.session import tenant_transaction
        from launchscope_api.infrastructure.repositories.material_ingestion import SqlAlchemyMaterialIngestionRepository
        from launchscope_domain.value_objects import TenantScope

        with tenant_transaction(self._session_factory, TenantScope(actor.tenant_id)) as session:
            return SqlAlchemyMaterialIngestionRepository(session).list_validated(actor.tenant_id, product_version_id)

    def _with_repository(self, record: MaterialRecord, action: Callable[[Any], None]) -> None:
        from launchscope_api.infrastructure.db.session import tenant_transaction
        from launchscope_api.infrastructure.repositories.material_ingestion import SqlAlchemyMaterialIngestionRepository
        from launchscope_domain.value_objects import TenantScope

        scope = TenantScope(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            product_version_id=record.product_version_id,
        )
        with tenant_transaction(self._session_factory, scope) as session:
            action(SqlAlchemyMaterialIngestionRepository(session))


def _sha256(value: str) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else ""
    if _SHA256.fullmatch(normalized) is None:
        raise MaterialValidationError("sha256 must be a 64-character lower-case hexadecimal digest")
    return normalized


def _validate_metadata(size_bytes: int, mime_type: str) -> None:
    if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or not 0 < size_bytes <= MAX_MATERIAL_BYTES:
        raise MaterialValidationError(f"size_bytes must be between 1 and {MAX_MATERIAL_BYTES}")
    if mime_type not in _ALLOWED_MIME_TYPES:
        raise MaterialValidationError("mime_type is not allowed for material intake")


__all__ = [
    "InMemoryQuarantineObjectStore",
    "MaterialIngestionApplication",
    "PersistentMaterialIngestionApplication",
    "MaterialRecord",
    "MaterialStatus",
    "MaterialValidationError",
    "ObjectMetadata",
    "QuarantineObjectStore",
    "UploadInitiation",
]
