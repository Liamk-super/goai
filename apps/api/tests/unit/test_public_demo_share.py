from uuid import UUID

import pytest
from fastapi import HTTPException

from launchscope_api.modules.experience.api import _public_share_actor

RESOURCE_ID = UUID("12c4112f-6c22-4bd1-960e-c678becc733c")


def test_public_demo_share_is_exact_resource_and_token_scoped(monkeypatch) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_SHARE_TOKEN", "x" * 64)
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_TENANT_ID", "1875f496-f34d-475f-9eae-0ef49109c29a")
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_ACTOR_ID", "local-demo:test-actor")
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_RESOURCE_IDS", str(RESOURCE_ID))

    actor = _public_share_actor(RESOURCE_ID, "x" * 64)

    assert actor.tenant_id == UUID("1875f496-f34d-475f-9eae-0ef49109c29a")
    assert actor.actor_id == "local-demo:test-actor"


@pytest.mark.parametrize(
    ("resource_id", "token"),
    [
        (RESOURCE_ID, "y" * 64),
        (UUID("9027a64b-f6e5-40ad-a31b-c161a0f8724e"), "x" * 64),
    ],
)
def test_public_demo_share_rejects_wrong_token_or_resource(monkeypatch, resource_id, token) -> None:
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_SHARE_TOKEN", "x" * 64)
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_TENANT_ID", "1875f496-f34d-475f-9eae-0ef49109c29a")
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_ACTOR_ID", "local-demo:test-actor")
    monkeypatch.setenv("LAUNCHSCOPE_PUBLIC_DEMO_RESOURCE_IDS", str(RESOURCE_ID))

    with pytest.raises(HTTPException) as exc_info:
        _public_share_actor(resource_id, token)

    assert exc_info.value.status_code == 404
