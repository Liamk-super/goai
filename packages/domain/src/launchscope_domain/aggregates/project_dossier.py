"""ProjectDossier aggregate and append-only product version history."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from ..enums import ProductVersionStatus
from ..errors import AppendOnlyViolation, InvariantViolation, TenantScopeViolation, ValidationError
from ..value_objects import TenantScope, _aware, _sha256, _text, _uuid


@dataclass(frozen=True, slots=True)
class MaterialMetadata:
    """Metadata for a quarantined material object; never the object body."""

    material_id: UUID
    scope: TenantScope
    object_key: str
    sha256: str
    mime_type: str
    size_bytes: int = 0
    display_name: str = "material"
    submitted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        object.__setattr__(self, "material_id", _uuid(self.material_id, "material_id"))
        object.__setattr__(self, "object_key", _text(self.object_key, "object_key", max_length=1024))
        object.__setattr__(self, "sha256", _sha256(self.sha256))
        object.__setattr__(self, "mime_type", _text(self.mime_type, "mime_type", max_length=255))
        object.__setattr__(self, "display_name", _text(self.display_name, "display_name", max_length=255))
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValidationError("size_bytes must be a non-negative integer", details={"field": "size_bytes"})
        object.__setattr__(self, "submitted_at", _aware(self.submitted_at, "submitted_at"))


@dataclass(frozen=True, slots=True)
class ProductProfile:
    """A versioned product profile snapshot."""

    profile_id: UUID
    scope: TenantScope
    product_version_id: UUID
    fields: Mapping[str, Any]
    confirmed_by: str
    confirmed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _uuid(self.profile_id, "profile_id"))
        object.__setattr__(self, "product_version_id", _uuid(self.product_version_id, "product_version_id"))
        if self.scope.product_version_id not in {None, self.product_version_id}:
            raise TenantScopeViolation(
                "profile is outside its ProductVersion scope",
                details={"product_version_id": str(self.product_version_id)},
            )
        if not isinstance(self.fields, Mapping):
            raise ValidationError("profile fields must be an object", details={"field": "fields"})
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))
        object.__setattr__(self, "confirmed_by", _text(self.confirmed_by, "confirmed_by", max_length=255))
        object.__setattr__(self, "confirmed_at", _aware(self.confirmed_at, "confirmed_at"))
        if self.supersedes_id is not None:
            object.__setattr__(self, "supersedes_id", _uuid(self.supersedes_id, "supersedes_id"))

    @classmethod
    def create(
        cls,
        scope: TenantScope,
        product_version_id: UUID | str,
        fields: Mapping[str, Any],
        confirmed_by: str,
        *,
        profile_id: UUID | str | None = None,
        supersedes_id: UUID | str | None = None,
        confirmed_at: datetime | None = None,
    ) -> ProductProfile:
        version_id = _uuid(product_version_id, "product_version_id")
        profile_scope = scope if scope.product_version_id == version_id else scope.with_product_version(version_id)
        return cls(
            profile_id=_uuid(profile_id or uuid4(), "profile_id"),
            scope=profile_scope,
            product_version_id=version_id,
            fields=fields,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at or datetime.now(UTC),
            supersedes_id=_uuid(supersedes_id, "supersedes_id") if supersedes_id else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.fields)


@dataclass
class ProductVersion:
    """A product version whose materials and profiles are historical."""

    product_version_id: UUID
    project_id: UUID
    label: str
    material_ids: tuple[UUID, ...] = ()
    scope: TenantScope | None = None
    status: ProductVersionStatus = ProductVersionStatus.DRAFT
    submitted_by: str | None = None
    submitted_at: datetime | None = None
    material_metadata: tuple[MaterialMetadata, ...] = ()
    profile_history: list[ProductProfile] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.product_version_id = _uuid(self.product_version_id, "product_version_id")
        self.project_id = _uuid(self.project_id, "project_id")
        self.label = _text(self.label, "label", max_length=100)
        self.material_ids = tuple(_uuid(value, "material_id") for value in self.material_ids)
        if len(set(self.material_ids)) != len(self.material_ids):
            raise InvariantViolation(
                "material_ids must be unique", details={"product_version_id": str(self.product_version_id)}
            )
        self.status = ProductVersionStatus(self.status)
        if self.scope is not None:
            if self.scope.project_id not in {None, self.project_id}:
                raise TenantScopeViolation("ProductVersion belongs to another project")
            if self.scope.product_version_id not in {None, self.product_version_id}:
                raise TenantScopeViolation("ProductVersion scope has another version id")
        if self.submitted_by is not None:
            self.submitted_by = _text(self.submitted_by, "submitted_by", max_length=255)
        if self.submitted_at is not None:
            self.submitted_at = _aware(self.submitted_at, "submitted_at")
        metadata_ids = tuple(item.material_id for item in self.material_metadata)
        if not set(metadata_ids).issubset(set(self.material_ids)):
            raise InvariantViolation("material metadata must belong to material_ids")
        self.material_metadata = tuple(self.material_metadata)
        self.profile_history = list(self.profile_history)

    @property
    def confirmed_profile(self) -> ProductProfile | None:
        return self.profile_history[-1] if self.profile_history else None

    @property
    def is_submitted(self) -> bool:
        return self.status is ProductVersionStatus.SUBMITTED

    def add_material(self, material: MaterialMetadata) -> None:
        if material.material_id in self.material_ids:
            raise AppendOnlyViolation("material is already attached to this ProductVersion")
        if self.scope is not None and (
            self.scope.tenant_id != material.scope.tenant_id
            or self.scope.workspace_id != material.scope.workspace_id
            or self.scope.project_id != material.scope.project_id
        ):
            raise TenantScopeViolation("material is outside ProductVersion scope")
        self.material_ids = (*self.material_ids, material.material_id)
        self.material_metadata = (*self.material_metadata, material)

    def submit(self, submitted_by: str, *, submitted_at: datetime | None = None) -> ProductVersion:
        if self.status is ProductVersionStatus.SUBMITTED:
            raise AppendOnlyViolation("ProductVersion has already been submitted")
        if not self.material_ids:
            raise InvariantViolation("a submitted ProductVersion must contain at least one material")
        self.status = ProductVersionStatus.SUBMITTED
        self.submitted_by = _text(submitted_by, "submitted_by", max_length=255)
        self.submitted_at = _aware(submitted_at, "submitted_at") if submitted_at else datetime.now(UTC)
        return self

    def confirm_profile(self, profile: ProductProfile) -> ProductProfile:
        if profile.product_version_id != self.product_version_id:
            raise TenantScopeViolation("profile belongs to another ProductVersion")
        if self.scope is not None and (
            self.scope.tenant_id != profile.scope.tenant_id
            or self.scope.workspace_id != profile.scope.workspace_id
            or self.scope.project_id != profile.scope.project_id
        ):
            raise TenantScopeViolation("profile is outside ProductVersion tenant scope")
        if self.confirmed_profile is not None:
            if profile.supersedes_id != self.confirmed_profile.profile_id:
                raise AppendOnlyViolation("a confirmed profile can only be superseded explicitly")
        elif profile.supersedes_id is not None:
            raise AppendOnlyViolation("a first profile cannot supersede another profile")
        if any(existing.profile_id == profile.profile_id for existing in self.profile_history):
            raise AppendOnlyViolation("profile_id is already present")
        self.profile_history.append(profile)
        return profile


@dataclass
class ProjectDossier:
    """Project root preserving ProductVersion and profile history."""

    scope: TenantScope
    name: str
    versions: dict[UUID, ProductVersion] = field(default_factory=dict)
    materials: dict[UUID, MaterialMetadata] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.scope.project_id is None or self.scope.workspace_id is None:
            raise ValidationError("ProjectDossier requires workspace_id and project_id")
        self.name = _text(self.name, "name", max_length=200)
        self.versions = dict(self.versions)
        self.materials = dict(self.materials)
        self.history = list(self.history)
        for version in self.versions.values():
            self._assert_version_scope(version)

    @classmethod
    def create(
        cls,
        scope: TenantScope,
        name: str,
    ) -> ProjectDossier:
        return cls(scope=scope, name=name)

    @property
    def dossier_id(self) -> UUID:
        assert self.scope.project_id is not None
        return self.scope.project_id

    @property
    def project_id(self) -> UUID:
        assert self.scope.project_id is not None
        return self.scope.project_id

    @property
    def product_versions(self) -> tuple[ProductVersion, ...]:
        return tuple(self.versions.values())

    @property
    def current_version(self) -> ProductVersion | None:
        return self.product_versions[-1] if self.product_versions else None

    def assert_scope(self, scope: TenantScope) -> None:
        if not self.scope.contains(scope) or scope.tenant_id != self.scope.tenant_id:
            raise TenantScopeViolation(
                "resource is outside ProjectDossier tenant scope",
                details={"expected_tenant_id": str(self.scope.tenant_id), "actual_tenant_id": str(scope.tenant_id)},
            )

    def add_material(self, material: MaterialMetadata) -> MaterialMetadata:
        self.assert_scope(material.scope)
        if material.material_id in self.materials:
            raise AppendOnlyViolation("material_id is already present in the dossier")
        self.materials[material.material_id] = material
        self.history.append(f"material.added:{material.material_id}")
        return material

    def add_product_version(self, version: ProductVersion) -> ProductVersion:
        self._assert_version_scope(version)
        if version.product_version_id in self.versions:
            raise AppendOnlyViolation("product_version_id is already present in the dossier")
        for material_id in version.material_ids:
            if material_id not in self.materials:
                raise InvariantViolation(
                    "ProductVersion references a material outside the dossier",
                    details={"material_id": str(material_id)},
                )
        self.versions[version.product_version_id] = version
        self.history.append(f"product_version.added:{version.product_version_id}")
        return version

    def create_product_version(
        self,
        label: str,
        material_ids: tuple[UUID, ...] = (),
        *,
        product_version_id: UUID | str | None = None,
    ) -> ProductVersion:
        version_scope = self.scope.with_product_version(product_version_id or uuid4())
        version = ProductVersion(
            product_version_id=version_scope.product_version_id or uuid4(),
            project_id=self.project_id,
            label=label,
            material_ids=material_ids,
            scope=version_scope,
        )
        return self.add_product_version(version)

    def submit_version(
        self,
        product_version_id: UUID | str,
        submitted_by: str,
        *,
        submitted_at: datetime | None = None,
    ) -> ProductVersion:
        version = self.get_version(product_version_id)
        version.submit(submitted_by, submitted_at=submitted_at)
        self.history.append(f"product_version.submitted:{version.product_version_id}")
        return version

    def confirm_profile(
        self,
        product_version_id: UUID | str,
        fields: Mapping[str, Any] | ProductProfile,
        confirmed_by: str | None = None,
        *,
        profile_id: UUID | str | None = None,
        supersedes_id: UUID | str | None = None,
    ) -> ProductProfile:
        version = self.get_version(product_version_id)
        if isinstance(fields, ProductProfile):
            profile = fields
        else:
            if confirmed_by is None:
                raise ValidationError("confirmed_by is required for a new profile")
            profile = ProductProfile.create(
                self.scope,
                version.product_version_id,
                fields,
                confirmed_by,
                profile_id=profile_id,
                supersedes_id=supersedes_id,
            )
        if not self.scope.contains(profile.scope):
            raise TenantScopeViolation("profile is outside dossier scope")
        version.confirm_profile(profile)
        self.history.append(f"profile.confirmed:{profile.profile_id}")
        return profile

    def get_version(self, product_version_id: UUID | str) -> ProductVersion:
        version_id = _uuid(product_version_id, "product_version_id")
        try:
            return self.versions[version_id]
        except KeyError as exc:
            raise ValidationError(
                "ProductVersion was not found in this dossier", details={"product_version_id": str(version_id)}
            ) from exc

    def _assert_version_scope(self, version: ProductVersion) -> None:
        if version.project_id != self.project_id:
            raise TenantScopeViolation("ProductVersion belongs to another project")
        if version.scope is not None and not self.scope.contains(version.scope):
            raise TenantScopeViolation("ProductVersion is outside dossier scope")
