"""T9 controlled-memory promotion policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from launchscope_api.infrastructure.db.schema import memory_item
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.memory_rag.candidate_application import (
    MemoryCandidateApplication,
    MemoryCandidateInput,
    MemoryPromotionError,
)


def test_time_sensitive_memory_requires_expiry_before_it_can_be_submitted() -> None:
    candidate = MemoryCandidateInput(item_type="PRICE", content={"price": "10"}, user_confirmed=True)
    with pytest.raises(MemoryPromotionError, match="valid_until"):
        MemoryCandidateApplication._validate_candidate(candidate)


def test_simulated_memory_must_keep_its_label() -> None:
    candidate = MemoryCandidateInput(
        item_type="FACT",
        content={"claim": "synthetic"},
        user_confirmed=True,
        simulated=True,
        valid_until=datetime.now(UTC) + timedelta(days=1),
    )
    with pytest.raises(MemoryPromotionError, match="simulated label"):
        MemoryCandidateApplication._validate_candidate(candidate)


def test_non_confirmed_memory_requires_evidence_bound_finding() -> None:
    candidate = MemoryCandidateInput(item_type="FACT", content={"claim": "not confirmed"})
    with pytest.raises(MemoryPromotionError, match="evidence-bound finding"):
        MemoryCandidateApplication._validate_candidate(candidate)


def test_user_confirmed_memory_promotion_persists_project_version_scope(
    database, runtime_engine, tenant_records
) -> None:
    scope = tenant_records["scope"]
    factory = session_factory(runtime_engine)
    with tenant_transaction(factory, scope) as session:
        application = MemoryCandidateApplication(session)
        candidate_id = application.submit(
            scope,
            MemoryCandidateInput(
                item_type="FACT",
                content={"claim": "confirmed market"},
                user_confirmed=True,
                region="CN",
            ),
        )
        promoted = application.promote(scope, candidate_id)
        row = session.execute(select(memory_item).where(memory_item.c.id == promoted.memory_id)).mappings().one()

    assert row["project_id"] == scope.project_id
    assert row["product_version_id"] == scope.product_version_id
    assert row["permission_scope"] == "PROJECT_MEMBER"
