"""Durable quarantine records backed by tenant-scoped PostgreSQL transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from launchscope_api.modules.project_dossier.material_ingestion import MaterialRecord, MaterialStatus

from ..db.schema import material, product_version, project


class SqlAlchemyMaterialIngestionRepository:
    """Stores only upload expectations and verified object metadata, never bytes."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, record: MaterialRecord) -> None:
        self.session.execute(
            material.insert().values(
                id=record.material_id,
                tenant_id=record.tenant_id,
                product_version_id=record.product_version_id,
                source_type="MATERIAL",
                object_key=record.object_key,
                sha256=record.expected_sha256,
                size_bytes=record.expected_size_bytes,
                mime_type=record.expected_mime_type,
                display_name=record.display_name,
                trust_level="E0",
                ingest_status=record.status.value,
                rejection_reason=record.rejection_reason,
                object_metadata={},
                submitted_at=datetime.now(UTC),
                created_at=datetime.now(UTC),
            )
        )

    def get(self, material_id: UUID, tenant_id: UUID) -> MaterialRecord | None:
        row = (
            self.session.execute(
                select(
                    material,
                    product_version.c.project_id.label("project_id"),
                    project.c.workspace_id.label("workspace_id"),
                )
                .join(product_version, product_version.c.id == material.c.product_version_id)
                .join(project, project.c.id == product_version.c.project_id)
                .where(material.c.id == material_id, material.c.tenant_id == tenant_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return MaterialRecord(
            material_id=row["id"],
            tenant_id=row["tenant_id"],
            workspace_id=row["workspace_id"],
            project_id=row["project_id"],
            product_version_id=row["product_version_id"],
            object_key=row["object_key"],
            display_name=row["display_name"],
            expected_sha256=row["sha256"],
            expected_size_bytes=row["size_bytes"],
            expected_mime_type=row["mime_type"],
            status=MaterialStatus(row["ingest_status"]),
            rejection_reason=row["rejection_reason"],
        )

    def complete(self, record: MaterialRecord, *, metadata: dict[str, str] | None = None) -> bool:
        result = self.session.execute(
            update(material)
            .where(
                material.c.id == record.material_id,
                material.c.tenant_id == record.tenant_id,
                material.c.ingest_status == MaterialStatus.UPLOADING.value,
            )
            .values(
                ingest_status=record.status.value,
                rejection_reason=record.rejection_reason,
                object_metadata=metadata or {},
            )
        )
        return getattr(result, "rowcount", 0) == 1

    def list_validated(self, tenant_id: UUID, product_version_id: UUID) -> tuple[MaterialRecord, ...]:
        rows = (
            self.session.execute(
                select(material.c.id)
                .where(
                    material.c.tenant_id == tenant_id,
                    material.c.product_version_id == product_version_id,
                    material.c.ingest_status == MaterialStatus.VALIDATED.value,
                )
                .order_by(material.c.created_at)
            )
            .scalars()
            .all()
        )
        return tuple(item for material_id in rows if (item := self.get(material_id, tenant_id)) is not None)


__all__ = ["SqlAlchemyMaterialIngestionRepository"]
