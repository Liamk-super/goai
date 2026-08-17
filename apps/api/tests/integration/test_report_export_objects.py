from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import text

from launchscope_api.infrastructure.db.session import session_factory
from launchscope_api.modules.decision_report.export_application import ExportRequest, ReportExportApplication
from launchscope_api.modules.identity_tenant.application import Actor

from .conftest import seed_tenant


class MemoryObjects:
    def __init__(self) -> None:
        self.values: dict[str, tuple[bytes, str, str]] = {}

    def put_private(self, key: str, body: bytes, mime_type: str) -> str:
        digest = hashlib.sha256(body).hexdigest()
        self.values[key] = (body, digest, mime_type)
        return digest

    def get_private(self, key: str, *, max_bytes: int = 2_000_000) -> bytes:
        body = self.values[key][0]
        assert len(body) <= max_bytes
        return body

    def head(self, key: str):
        item = self.values.get(key)
        if item is None:
            return None
        return SimpleNamespace(size_bytes=len(item[0]), sha256=item[1], mime_type=item[2])

    def signed_read_url(self, key: str) -> str:
        return f"https://objects.invalid/{key}"


class Renderer:
    version = "integration-renderer-v1"

    def __init__(self) -> None:
        self.calls = 0

    def render_pdf(self, target) -> bytes:
        self.calls += 1
        return b"%PDF-1.7\n" + target.source_sha256.encode()


def test_report_export_catalog_and_object_are_tenant_scoped_hash_verified_and_cached(database, runtime_engine) -> None:
    seeded = seed_tenant(database)
    tenant_id = UUID(str(seeded["tenant_id"]))
    run_id = UUID(str(seeded["run_id"]))
    report_id, decision_id = uuid4(), uuid4()
    objects = MemoryObjects()
    document = {
        "schema_version": "2.0",
        "report_id": str(report_id),
        "run_id": str(run_id),
        "product_title": "PostgreSQL export",
        "source_sha256": "a" * 64,
        "agent_report_cards": [],
        "citations": [],
        "source_directory": [],
    }
    source_body = json.dumps(document, sort_keys=True).encode()
    source_key = f"{tenant_id}/runs/{run_id}/reports/{report_id}.json"
    source_sha = objects.put_private(source_key, source_body, "application/json")
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO decision "
                "(id, tenant_id, run_id, recommendation, standard_version, dimension_grades, hard_blocks, created_at) "
                "VALUES (:id, :tenant_id, :run_id, 'VALIDATE_FURTHER', '1.0', '{}'::jsonb, '[]'::jsonb, :now)"
            ),
            {"id": decision_id, "tenant_id": tenant_id, "run_id": run_id, "now": datetime.now(UTC)},
        )
        connection.execute(
            text(
                "INSERT INTO report "
                "(id, tenant_id, run_id, decision_id, object_key, sha256, status, action_items, created_at) "
                "VALUES (:id, :tenant_id, :run_id, :decision_id, :key, :sha, 'COMMITTED', '[]'::jsonb, :now)"
            ),
            {
                "id": report_id,
                "tenant_id": tenant_id,
                "run_id": run_id,
                "decision_id": decision_id,
                "key": source_key,
                "sha": source_sha,
                "now": datetime.now(UTC),
            },
        )

    renderer = Renderer()
    application = ReportExportApplication(session_factory(runtime_engine), objects, renderer)
    actor = Actor(tenant_id, "integration-owner")
    request = ExportRequest(kind="SUPERVISOR", view="FULL", locale="zh-CN")

    first = application.create(actor, report_id, request, idempotency_key=f"export-{report_id}")
    second = application.create(actor, report_id, request, idempotency_key=f"export-cache-{report_id}")
    read = application.read_url(actor, first.export_id)

    assert first.status == "COMPLETED"
    assert first.export_id == second.export_id
    assert renderer.calls == 1
    assert read["sha256"] == first.sha256
    assert objects.get_private(str(first.object_key)).startswith(b"%PDF")
    with database.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT tenant_id, status, sha256, size_bytes FROM report_export_artifact "
                "WHERE id = :export_id"
            ),
            {"export_id": first.export_id},
        ).one()
    assert tuple(stored) == (tenant_id, "COMPLETED", first.sha256, first.size_bytes)
