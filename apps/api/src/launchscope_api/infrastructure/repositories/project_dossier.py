"""PostgreSQL adapter for the ProjectDossier aggregate."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from launchscope_domain.aggregates.project_dossier import (
    MaterialMetadata,
    ProductProfile,
    ProductVersion,
    ProjectDossier,
)
from launchscope_domain.enums import ProductVersionStatus
from launchscope_domain.ports.repositories import ProjectDossierRepository as ProjectDossierPort
from launchscope_domain.value_objects import TenantScope

from ..db.schema import material, product_profile, product_version, project
from .base import (
    assert_aggregate_scope,
    existing_row,
    insert_if_absent,
    json_value,
    require_scope_id,
    require_utc_datetime,
    utc_datetime,
)


class SqlAlchemyProjectDossierRepository(ProjectDossierPort):
    """Persist dossier metadata in one caller-owned transaction.

    The adapter never commits.  An application service can therefore save a
    state change and enqueue its Outbox event in the same transaction.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, resource_id: UUID, scope: TenantScope) -> ProjectDossier | None:
        row = (
            self.session.execute(
                select(project).where(project.c.id == resource_id, project.c.tenant_id == scope.tenant_id)
            )
            .mappings()
            .first()
        )
        if row is None or (scope.workspace_id is not None and row["workspace_id"] != scope.workspace_id):
            return None

        dossier_scope = TenantScope(
            tenant_id=scope.tenant_id,
            workspace_id=row["workspace_id"],
            project_id=row["id"],
        )
        version_rows = (
            self.session.execute(
                select(product_version)
                .where(
                    product_version.c.tenant_id == scope.tenant_id,
                    product_version.c.project_id == resource_id,
                )
                .order_by(product_version.c.version_number)
            )
            .mappings()
            .all()
        )
        version_ids = tuple(row["id"] for row in version_rows)
        material_rows = (
            self.session.execute(
                select(material).where(
                    material.c.tenant_id == scope.tenant_id,
                    material.c.product_version_id.in_(version_ids),
                )
            )
            .mappings()
            .all()
            if version_ids
            else []
        )
        material_by_version: dict[UUID, list[MaterialMetadata]] = {}
        all_materials: dict[UUID, MaterialMetadata] = {}
        for item in material_rows:
            item_scope = dossier_scope.with_product_version(item["product_version_id"])
            metadata = MaterialMetadata(
                material_id=item["id"],
                scope=item_scope,
                object_key=item["object_key"],
                sha256=item["sha256"],
                mime_type=item["mime_type"],
                size_bytes=item["size_bytes"],
                display_name=item["display_name"],
                submitted_at=require_utc_datetime(item["submitted_at"]),
            )
            material_by_version.setdefault(item["product_version_id"], []).append(metadata)
            all_materials[metadata.material_id] = metadata

        versions: dict[UUID, ProductVersion] = {}
        for item in version_rows:
            version_scope = dossier_scope.with_product_version(item["id"])
            attached = tuple(material_by_version.get(item["id"], ()))
            profiles = (
                self.session.execute(
                    select(product_profile)
                    .where(
                        product_profile.c.tenant_id == scope.tenant_id,
                        product_profile.c.product_version_id == item["id"],
                    )
                    .order_by(product_profile.c.confirmed_at)
                )
                .mappings()
                .all()
            )
            profile_history = [
                ProductProfile(
                    profile_id=profile["id"],
                    scope=version_scope,
                    product_version_id=item["id"],
                    fields=profile["confirmed_fields"],
                    confirmed_by=profile["confirmed_by"],
                    confirmed_at=require_utc_datetime(profile["confirmed_at"]),
                    supersedes_id=profile["supersedes_id"],
                )
                for profile in profiles
            ]
            versions[item["id"]] = ProductVersion(
                product_version_id=item["id"],
                project_id=resource_id,
                label=item["label"],
                material_ids=tuple(value.material_id for value in attached),
                scope=version_scope,
                status=ProductVersionStatus(item["status"]),
                submitted_by=item["submitted_by"],
                submitted_at=utc_datetime(item["submitted_at"]),
                material_metadata=attached,
                profile_history=profile_history,
            )

        return ProjectDossier(
            scope=dossier_scope,
            name=row["name"],
            versions=versions,
            materials=all_materials,
        )

    def save(self, aggregate: ProjectDossier) -> None:
        scope = aggregate.scope
        project_id = require_scope_id(scope, "project_id")
        workspace_id = require_scope_id(scope, "workspace_id")
        row = existing_row(self.session, project, project_id, scope)
        now = datetime.now(UTC)
        if row is None:
            self.session.execute(
                project.insert().values(
                    id=project_id,
                    tenant_id=scope.tenant_id,
                    workspace_id=workspace_id,
                    name=aggregate.name,
                    dossier_status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            self.session.execute(
                update(project)
                .where(project.c.id == project_id, project.c.tenant_id == scope.tenant_id)
                .values(name=aggregate.name, updated_at=now)
            )

        for ordinal, version in enumerate(aggregate.product_versions, start=1):
            if version.scope is not None:
                assert_aggregate_scope(version, scope)
            version_scope = version.scope or scope.with_product_version(version.product_version_id)
            insert_if_absent(
                self.session,
                product_version,
                {
                    "id": version.product_version_id,
                    "tenant_id": scope.tenant_id,
                    "project_id": project_id,
                    "version_number": ordinal,
                    "label": version.label,
                    "stage": "SUBMITTED" if version.is_submitted else "DRAFT",
                    "status": version.status.value,
                    "submitted_by": version.submitted_by,
                    "submitted_at": version.submitted_at,
                    "created_at": now,
                },
                resource_id=version.product_version_id,
            )
            self.session.execute(
                update(product_version)
                .where(
                    product_version.c.id == version.product_version_id,
                    product_version.c.tenant_id == scope.tenant_id,
                )
                .values(
                    label=version.label,
                    stage="SUBMITTED" if version.is_submitted else "DRAFT",
                    status=version.status.value,
                    submitted_by=version.submitted_by,
                    submitted_at=version.submitted_at,
                )
            )
            for material_id in version.material_ids:
                metadata = aggregate.materials.get(material_id)
                if metadata is None:
                    raise ValueError(f"material {material_id} is missing from the dossier")
                assert_aggregate_scope(metadata, scope)
                existing_material = self.session.execute(
                    select(material.c.id).where(
                        material.c.id == metadata.material_id,
                        material.c.tenant_id == scope.tenant_id,
                    )
                ).first()
                if existing_material is None:
                    self.session.execute(
                        material.insert().values(
                            id=metadata.material_id,
                            tenant_id=scope.tenant_id,
                            product_version_id=version_scope.product_version_id,
                            source_type="MATERIAL",
                            object_key=metadata.object_key,
                            sha256=metadata.sha256,
                            size_bytes=metadata.size_bytes,
                            mime_type=metadata.mime_type,
                            display_name=metadata.display_name,
                            trust_level="E0",
                            ingest_status="QUARANTINED",
                            submitted_at=metadata.submitted_at,
                            created_at=metadata.submitted_at,
                        )
                    )
            for profile in version.profile_history:
                assert_aggregate_scope(profile, scope)
                insert_if_absent(
                    self.session,
                    product_profile,
                    {
                        "id": profile.profile_id,
                        "tenant_id": scope.tenant_id,
                        "product_version_id": version.product_version_id,
                        "confirmed_fields": json_value(profile.fields),
                        "confirmation_status": "CONFIRMED",
                        "confirmed_by": profile.confirmed_by,
                        "confirmed_at": profile.confirmed_at,
                        "supersedes_id": profile.supersedes_id,
                        "created_at": profile.confirmed_at,
                    },
                    resource_id=profile.profile_id,
                )


ProjectDossierRepositoryAdapter = SqlAlchemyProjectDossierRepository

__all__ = ["ProjectDossierRepositoryAdapter", "SqlAlchemyProjectDossierRepository"]
