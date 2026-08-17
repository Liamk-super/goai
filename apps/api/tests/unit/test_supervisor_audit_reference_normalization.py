from launchscope_api.modules.supervisor.audit_application import (
    _apply_canonical_evidence_refs,
    _normalize_specialist_report_document,
)


def test_sha_bound_task_evidence_canonicalizes_redundant_long_refs() -> None:
    submitted = "tenant/t/run/r/task/516de944-fee0-45ba-90e2eb812de12948/evidence/e.png"
    canonical = "tenant/t/run/r/task/516de944-fee0-45ba-90e2-eb812de12948/evidence/e.png"
    digest = "a" * 64
    document = {
        "evidence_refs": [{"ref": submitted, "sha256": digest}],
        "report_ref": {"ref": submitted, "sha256": digest},
        "findings": [{"evidence_refs": [submitted]}],
    }

    _apply_canonical_evidence_refs(document, {digest: canonical})

    assert document["evidence_refs"] == [{"ref": canonical, "sha256": digest}]
    assert document["report_ref"] == {"ref": canonical, "sha256": digest}
    assert document["findings"][0]["evidence_refs"] == [canonical]


def test_sha_bound_task_evidence_preserves_distinct_objects_with_the_same_sha() -> None:
    digest = "b" * 64
    first = "tenant/run/task/evidence/first.json"
    second = "tenant/run/task/evidence/second.json"
    document = {
        "evidence_refs": [
            {"ref": first, "sha256": digest},
            {"ref": second, "sha256": digest},
        ],
        "report_ref": {"ref": first, "sha256": digest},
        "findings": [{"evidence_refs": [first, second]}],
    }

    _apply_canonical_evidence_refs(document, {first: first, second: second})

    assert document["evidence_refs"] == [
        {"ref": first, "sha256": digest},
        {"ref": second, "sha256": digest},
    ]
    assert document["findings"][0]["evidence_refs"] == [first, second]


def test_specialist_report_normalization_projects_legacy_metadata_onto_v22_identity() -> None:
    source = {
        "schema_version": "2.0",
        "report_id": "report-id",
        "tenant_id": "tenant-id",
        "task_id": "task-id",
        "standard_version": "2.2",
        "generated_at": "2026-08-15T00:00:00Z",
        "as_of": "2026-08-15T00:00:00Z",
        "region_scope": "GLOBAL",
        "product_version": "product-version-id",
        "agent_code": "user-evidence",
    }

    normalized = _normalize_specialist_report_document(source, product_title="CreaTrades（演示验收）")

    assert normalized == {
        "schema_version": "2.0",
        "report_id": "report-id",
        "agent_code": "user-evidence",
        "product_version_id": "product-version-id",
        "product_title": "CreaTrades（演示验收）",
    }
    assert source["tenant_id"] == "tenant-id"
