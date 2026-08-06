"""T9 RAG scope checks run before ranking and fail closed on incomplete metadata."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from launchscope_api.infrastructure.db.schema import memory_item, rag_retrieval
from launchscope_api.infrastructure.db.session import session_factory, tenant_transaction
from launchscope_api.modules.memory_rag.indexing import MemoryRagIndex
from launchscope_api.modules.memory_rag.retrieval_policy import RetrievalPolicy, RetrievalPolicyError, RetrievalScope
from launchscope_domain.value_objects import TenantScope


def _scope() -> TenantScope:
    return TenantScope(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())


def test_rag_scope_rejects_missing_permissions_and_excludes_cross_boundary_rows() -> None:
    scope = _scope()
    with pytest.raises(RetrievalPolicyError, match="permissions"):
        RetrievalScope.create(scope, region="CN", permissions=set())
    request = RetrievalScope.create(scope, region="CN", permissions={"PROJECT_MEMBER"})
    row = {
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "product_version_id": scope.product_version_id,
        "region": "CN",
        "permission_scope": "PROJECT_MEMBER",
        "validity_status": "ACTIVE",
        "valid_until": datetime.now(UTC) + timedelta(days=1),
    }
    policy = RetrievalPolicy()
    assert policy.permits_row(request, row)
    row["product_version_id"] = uuid4()
    assert not policy.permits_row(request, row)
    row["product_version_id"] = scope.product_version_id
    row["valid_until"] = datetime.now(UTC) - timedelta(seconds=1)
    assert not policy.permits_row(request, row)


def test_rag_records_scope_filters_and_hash_in_postgresql(database, runtime_engine, tenant_records) -> None:
    scope = tenant_records["scope"]
    factory = session_factory(runtime_engine)
    with tenant_transaction(factory, scope) as session:
        memory_id = uuid4()
        session.execute(
            memory_item.insert().values(
                id=memory_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                product_version_id=scope.product_version_id,
                source_finding_id=None,
                item_type="FACT",
                validity_status="ACTIVE",
                valid_until=datetime.now(UTC) + timedelta(days=1),
                region="CN",
                permission_scope="PROJECT_MEMBER",
                search_text="validated checkout latency",
                content={"claim": "validated checkout latency"},
                created_at=datetime.now(UTC),
            )
        )
        request = RetrievalScope.create(scope, region="CN", permissions={"PROJECT_MEMBER"})
        result = MemoryRagIndex(session).retrieve(request, "checkout latency")
        audit = session.execute(select(rag_retrieval).where(rag_retrieval.c.id == result.retrieval_id)).mappings().one()

    assert [hit.memory_id for hit in result.hits] == [memory_id]
    assert audit["filters"]["product_version_id"] == str(scope.product_version_id)
    assert audit["result_sha256"] == result.result_hash
