from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest

from launchscope_api.modules.experience.public_share import (
    PublicDemoShareGrant,
    PublicDemoShareResolver,
    PublicShareNotFound,
)


def _grant(*, agents: bool = True, evidence: bool = True) -> PublicDemoShareGrant:
    return PublicDemoShareGrant(
        tenant_id=uuid4(),
        share_id=uuid4(),
        run_id=uuid4(),
        report_id=uuid4(),
        include_agent_reports=agents,
        include_evidence=evidence,
    )


def test_public_supervisor_share_is_bound_to_one_exact_report_before_database_access() -> None:
    resolver = PublicDemoShareResolver(cast(Any, None))
    grant = _grant()

    with pytest.raises(PublicShareNotFound, match="not found"):
        resolver.supervisor_metadata(grant, uuid4())


def test_public_share_flags_fail_closed_before_agent_or_evidence_access() -> None:
    resolver = PublicDemoShareResolver(cast(Any, None))
    grant = _grant(agents=False, evidence=False)

    with pytest.raises(PublicShareNotFound):
        resolver.agent_metadata(grant, "user-evidence")
    with pytest.raises(PublicShareNotFound):
        resolver.evidence_metadata(grant, uuid4())

