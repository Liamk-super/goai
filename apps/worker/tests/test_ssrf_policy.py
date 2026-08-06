from __future__ import annotations

import pytest

from launchscope_worker.tool_gateway.contract import ToolContract
from launchscope_worker.tools.public_research import (
    HttpResponse,
    PublicResearchClient,
    PublicResearchPolicyError,
    validate_public_https_url,
)


def _public(_: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://10.0.0.1",
        "https://metadata.google.internal",
    ],
)
def test_loopback_private_metadata_and_non_https_are_rejected(url: str) -> None:
    with pytest.raises(PublicResearchPolicyError):
        validate_public_https_url(url, ("example.com", "google.internal"), _public)


def test_dns_rebinding_and_unvalidated_redirect_are_rejected() -> None:
    with pytest.raises(PublicResearchPolicyError, match="DNS result"):
        validate_public_https_url("https://example.com", ("example.com",), lambda host: ["127.0.0.1"])
    contract = ToolContract(
        "public-research.get.v1",
        "1.0",
        "public_research.read",
        "PUBLIC_RESEARCH",
        True,
        30,
        0,
        ("example.com",),
        1,
        1000,
    )
    client = PublicResearchClient(
        transport=lambda url, method, timeout, maximum: HttpResponse(url, 302, {"Location": "https://127.0.0.1/"}, b""),
        resolver=_public,
    )
    with pytest.raises(PublicResearchPolicyError):
        client.fetch({"url": "https://example.com", "method": "GET"}, contract)
