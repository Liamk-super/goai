from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from launchscope_domain import TenantScope


@pytest.fixture
def scope() -> TenantScope:
    return TenantScope(
        tenant_id=UUID("10000000-0000-4000-8000-000000000001"),
        workspace_id=UUID("20000000-0000-4000-8000-000000000001"),
        project_id=UUID("30000000-0000-4000-8000-000000000001"),
        product_version_id=UUID("40000000-0000-4000-8000-000000000001"),
        run_id=UUID("50000000-0000-4000-8000-000000000001"),
    )


@pytest.fixture
def other_scope() -> TenantScope:
    return TenantScope(tenant_id=uuid4(), workspace_id=uuid4(), project_id=uuid4())
