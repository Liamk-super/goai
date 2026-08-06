"""T12 release-level replay of the highest-risk fail-closed boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from launchscope_observability import REDACTED, redact
from launchscope_worker.runtime.sandbox import SandboxPolicy, SandboxViolation
from launchscope_worker.tools.public_research import PublicResearchPolicyError, validate_public_https_url


def test_secret_prompt_and_reasoning_are_redacted_recursively() -> None:
    result = redact(
        {"access_token": "token", "prompt": "body", "nested": {"private_reasoning": "thought"}}
    )
    assert result == {
        "access_token": REDACTED,
        "prompt": REDACTED,
        "nested": {"private_reasoning": REDACTED},
    }


@pytest.mark.parametrize(
    "url",
    (
        "http://example.com/source",
        "https://localhost/source",
        "https://127.0.0.1/source",
        "https://169.254.169.254/latest/meta-data",
        "https://metadata.google.internal/computeMetadata/v1",
    ),
)
def test_ssrf_and_non_https_targets_fail_closed(url: str) -> None:
    with pytest.raises(PublicResearchPolicyError):
        validate_public_https_url(url, ("example.com",), resolver=lambda _host: ["93.184.216.34"])


def test_repository_sandbox_rejects_path_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(SandboxViolation):
        SandboxPolicy.for_repository(root).resolve_read_path("../secret.txt")
