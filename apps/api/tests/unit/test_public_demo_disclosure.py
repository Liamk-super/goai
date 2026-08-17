from __future__ import annotations

from uuid import UUID, uuid4

from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.api import (
    accept_public_demo_disclosure,
    get_public_demo_disclosure,
)


class _Dossier:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, UUID]] = []

    def public_demo_disclosure(self, _actor: Actor, version_id: UUID) -> dict[str, object]:
        return {
            "product_version_id": str(version_id),
            "policy_version": "public-demo-evidence-v1",
            "accepted": False,
            "acceptance_id": None,
            "accepted_at": None,
        }

    def accept_public_demo_disclosure(
        self,
        _actor: Actor,
        version_id: UUID,
        *,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> dict[str, object]:
        self.calls.append((version_id, idempotency_key, correlation_id))
        return {
            "product_version_id": str(version_id),
            "policy_version": "public-demo-evidence-v1",
            "accepted": True,
            "acceptance_id": str(uuid4()),
            "accepted_at": "2026-08-13T00:00:00+00:00",
        }


def test_disclosure_status_is_read_before_upload() -> None:
    version_id = uuid4()
    result = get_public_demo_disclosure(version_id, Actor(uuid4(), "alice"), _Dossier())

    assert result == {
        "product_version_id": str(version_id),
        "policy_version": "public-demo-evidence-v1",
        "accepted": False,
        "acceptance_id": None,
        "accepted_at": None,
    }


def test_one_button_acceptance_forwards_required_idempotency_and_correlation_headers() -> None:
    dossier = _Dossier()
    version_id, correlation_id = uuid4(), uuid4()

    result = accept_public_demo_disclosure(
        version_id,
        Actor(uuid4(), "alice"),
        correlation_id,
        "disclosure:version:policy-v1",
        dossier,
    )

    assert result["accepted"] is True
    assert dossier.calls == [(version_id, "disclosure:version:policy-v1", correlation_id)]

