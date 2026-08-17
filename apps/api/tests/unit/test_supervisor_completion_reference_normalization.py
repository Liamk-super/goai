from uuid import uuid4

import pytest

from launchscope_api.modules.supervisor.completion_application import (
    CompletionValidationError,
    _apply_canonical_synthesis_evidence_citations,
    _bind_report_citations,
)


def test_synthesis_evidence_uuid_canonicalizes_to_immutable_object_ref() -> None:
    evidence_id = "7763bee6-fe6b-43a4-a8ae-5c2b84256b5a"
    canonical = "tenant/t/run/r/task/k/evidence/e.png"
    document = {
        "citations": [
            {"kind": "FINDING", "ref": "finding-id"},
            {"kind": "EVIDENCE", "ref": evidence_id},
            {"kind": "EVIDENCE", "ref": f"evidence:{evidence_id}"},
            {"kind": "EVIDENCE", "ref": canonical},
        ]
    }

    _apply_canonical_synthesis_evidence_citations(document, {evidence_id: canonical})

    assert document["citations"] == [
        {"kind": "FINDING", "ref": "finding-id"},
        {"kind": "EVIDENCE", "ref": canonical},
        {"kind": "EVIDENCE", "ref": canonical},
        {"kind": "EVIDENCE", "ref": canonical},
    ]


def test_synthesis_unknown_evidence_uuid_is_left_for_strict_validation() -> None:
    document = {"citations": [{"kind": "EVIDENCE", "ref": "unknown"}]}

    _apply_canonical_synthesis_evidence_citations(document, {})

    assert document["citations"] == [{"kind": "EVIDENCE", "ref": "unknown"}]


def test_manager_evidence_alias_binds_unique_claim_citations_to_audited_sources() -> None:
    evidence_id = uuid4()
    product_audit_id, user_audit_id = uuid4(), uuid4()
    document = {
        "claims": [
            {
                "claim_id": "claim-product",
                "section": "PRODUCT",
                "citation_ids": [f"citation-{evidence_id}"],
            },
            {
                "claim_id": "claim-user",
                "section": "USER",
                "citation_ids": [f"citation-{evidence_id}"],
            },
        ]
    }
    bases = [
        {
            "citation_id": f"citation-{audit_id.hex}-1",
            "source_claim_id": f"claim-{audit_id}",
            "evidence_id": str(evidence_id),
            "source_locator_id": str(audit_id),
            "support_role": "SUPPORT",
            "audit_status": "DOWNGRADED",
            "label": 1,
        }
        for audit_id in (product_audit_id, user_audit_id)
    ]
    audited = [
        {"id": product_audit_id, "finding": {"agent_code": "product-engineering"}},
        {"id": user_audit_id, "finding": {"agent_code": "user-evidence"}},
    ]

    normalized, citations = _bind_report_citations(document, bases, audited)

    assert normalized["claims"][0]["citation_ids"] == ["citation-product-1"]
    assert normalized["claims"][1]["citation_ids"] == ["citation-user-1"]
    assert [item["citation_id"] for item in citations] == ["citation-product-1", "citation-user-1"]
    assert [item["source_locator_id"] for item in citations] == [str(product_audit_id), str(user_audit_id)]
    assert citations[0]["evidence_id"] == citations[1]["evidence_id"] == str(evidence_id)


def test_manager_unknown_citation_alias_remains_fail_closed() -> None:
    document = {"claims": [{"claim_id": "claim-one", "section": "CONCLUSION", "citation_ids": ["bad"]}]}

    with pytest.raises(CompletionValidationError, match="unknown audited Citation"):
        _bind_report_citations(document, [], [])


def test_manager_report_alias_binds_to_audited_source_and_drops_unverifiable_extra() -> None:
    evidence_id = uuid4()
    audited_id = uuid4()
    report_alias = f"citation-{uuid4()}"
    document = {
        "claims": [{
            "claim_id": "claim-user",
            "section": "USER",
            "citation_ids": [report_alias, f"citation-{uuid4()}"],
        }]
    }
    bases = [{
        "citation_id": f"citation-{audited_id.hex}-1",
        "aliases": [report_alias],
        "source_claim_id": f"claim-{audited_id}",
        "evidence_id": str(evidence_id),
        "source_locator_id": str(uuid4()),
        "support_role": "SUPPORT",
        "audit_status": "DOWNGRADED",
        "label": 1,
    }]
    audited = [{"id": audited_id, "finding": {"agent_code": "user-evidence"}}]

    normalized, citations = _bind_report_citations(document, bases, audited)

    assert normalized["claims"][0]["citation_ids"] == ["citation-user-1"]
    assert citations[0]["evidence_id"] == str(evidence_id)
    assert "aliases" not in citations[0]
