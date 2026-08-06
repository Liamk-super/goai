"""T11 maintenance deletion clears bodies while retaining a hash-only tombstone."""

from sqlalchemy import select

from launchscope_api.infrastructure.db.schema import deletion_tombstone, evidence, memory_item, trace_metadata
from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.modules.audit_compliance.retention_application import RetentionApplication, RetentionPolicy


class RecordingObjectStore:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, object_key: str) -> bool:
        self.deleted.append(object_key)
        return True


def test_default_and_tenant_retention_policy(database, tenant_records) -> None:
    app = RetentionApplication(session_factory(database), RecordingObjectStore())
    tenant_id = tenant_records["tenant_id"]
    assert app.policy(tenant_id) == RetentionPolicy()
    override = RetentionPolicy(
        temporary_days=5, evidence_days=120, trace_body_days=14, metrics_days=400, audit_days=730
    )
    assert app.configure_policy(tenant_id, override) == override
    assert app.policy(tenant_id) == override


def test_run_deletion_clears_object_body_memory_and_trace_but_keeps_tombstone(database, tenant_records) -> None:
    objects = RecordingObjectStore()
    app = RetentionApplication(session_factory(database), objects)
    report = app.delete_run(
        tenant_records["tenant_id"],
        tenant_records["run_id"],
        actor_id="retention-worker",
        reason="automated test cleanup",
    )
    assert report.objects == 1
    assert report.database_bodies >= 2
    assert report.cache == "not_configured"
    assert objects.deleted and objects.deleted[0].endswith("evidence.txt")
    with database.connect() as connection:
        evidence_row = connection.execute(
            select(evidence.c.object_key, evidence.c.summary).where(
                evidence.c.tenant_id == tenant_records["tenant_id"],
                evidence.c.run_id == tenant_records["run_id"],
            )
        ).first()
        assert evidence_row.object_key.startswith("deleted/")
        assert evidence_row.summary == "[deleted by retention policy]"
        assert connection.execute(
            select(memory_item.c.content).where(memory_item.c.tenant_id == tenant_records["tenant_id"])
        ).scalar_one() == {}
        assert connection.execute(
            select(trace_metadata.c.id).where(
                trace_metadata.c.tenant_id == tenant_records["tenant_id"],
                trace_metadata.c.run_id == tenant_records["run_id"],
            )
        ).first() is None
        tombstone = connection.execute(
            select(deletion_tombstone).where(deletion_tombstone.c.tenant_id == tenant_records["tenant_id"])
        ).mappings().one()
        assert tombstone["target_sha256"] and len(tombstone["target_sha256"]) == 64
        assert "evidence.txt" not in str(tombstone["result"])


def test_project_deletion_resolves_versions_before_redacting(database, tenant_records) -> None:
    objects = RecordingObjectStore()
    app = RetentionApplication(session_factory(database), objects)

    report = app.delete_project(
        tenant_records["tenant_id"],
        tenant_records["project_id"],
        actor_id="retention-worker",
        reason="project-scope retention acceptance",
    )

    assert report.target_type == "PROJECT"
    assert report.objects >= 1
    assert report.database_bodies >= 2
    assert objects.deleted
