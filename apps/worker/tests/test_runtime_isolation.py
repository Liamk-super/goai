from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from launchscope_worker.runtime.lease import LeaseConflict, LeaseRegistry
from launchscope_worker.runtime.sandbox import SandboxPolicy, SandboxViolation
from launchscope_worker.tools.repository_read import RepositoryReader


def test_worker_defaults_to_no_network_subprocess_or_path_escape(tmp_path: Path) -> None:
    policy = SandboxPolicy.for_repository(tmp_path)
    with pytest.raises(SandboxViolation, match="ambient Worker network"):
        policy.require_network_gateway("public-research.get.v1")
    with pytest.raises(SandboxViolation, match="subprocess"):
        policy.require_no_subprocess()
    with pytest.raises(SandboxViolation, match="escapes"):
        policy.resolve_read_path("../outside.txt")
    assert RepositoryReader(tmp_path).sandbox.network_enabled is False


def test_short_lease_is_compare_and_swap_and_expiry_is_not_reused() -> None:
    registry, task_id = LeaseRegistry(), uuid4()
    now = datetime(2026, 8, 5, tzinfo=UTC)
    lease = registry.acquire(task_id, "worker-a", ttl_seconds=30, now=now)
    with pytest.raises(LeaseConflict):
        registry.acquire(task_id, "worker-b", ttl_seconds=30, now=now + timedelta(seconds=1))
    with pytest.raises(LeaseConflict):
        registry.require_active(task_id, lease.token, now=now + timedelta(seconds=31))
