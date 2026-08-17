"""Read-only Tool evidence with an explicit external-network acceptance boundary."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from launchscope_worker.tool_gateway.contract import ToolContractRegistry
from launchscope_worker.tools.public_research import PublicResearchClient
from launchscope_worker.tools.repository_read import RepositoryReader

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "e2e" / "fixtures"


def test_repository_reader_executes_a_real_bounded_read_without_network() -> None:
    contract = ToolContractRegistry().load("repository.read.v1")
    result = RepositoryReader(FIXTURE_ROOT).read(
        {"path": "v1/product-materials/brief.md", "max_bytes": 65536}, contract
    )
    assert result.result["path"] == "v1/product-materials/brief.md"
    assert len(result.result["sha256"]) == 64
    assert "external user interviews" in result.result["content"]
    assert result.evidence["source_type"] == "REPOSITORY"


def test_external_public_https_read_requires_an_explicit_authorized_case() -> None:
    url = os.getenv("LAUNCHSCOPE_REAL_READONLY_URL")
    if not url:
        pytest.skip("BLOCKED: no explicitly authorized external read-only main-case URL")
    host = urlsplit(url).hostname
    if not host:
        pytest.fail("LAUNCHSCOPE_REAL_READONLY_URL must be an absolute HTTPS URL")
    base = ToolContractRegistry().load("public-research.get.v1")
    contract = replace(base, allowed_domains=(host,))
    result = PublicResearchClient().fetch({"url": url, "method": "GET"}, contract)
    assert result.evidence is not None
    assert result.evidence["source_url"] == url
    assert len(result.evidence["sha256"]) == 64
