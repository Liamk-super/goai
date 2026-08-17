from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import uuid4

import pytest

from launchscope_api.modules.experience.api import get_agent_report
from launchscope_api.modules.identity_tenant.application import Actor
from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata
from launchscope_api.modules.user_validation.application import ArtifactIntegrityError, ReportTooLargeError


class _ReadModel:
    def __init__(self, body: bytes, *, mime_type: str = "application/json") -> None:
        self.body = body
        self.mime_type = mime_type

    def agent_report_metadata(self, _actor: Actor, _run_id, agent_code: str) -> dict[str, object]:
        return {
            "agent_code": agent_code,
            "title": "User evidence report",
            "kind": "DOMAIN",
            "object_key": "private/report.json",
            "sha256": hashlib.sha256(self.body).hexdigest(),
            "mime_type": self.mime_type,
            "created_at": "2026-08-12T00:00:00Z",
            "audit_round": None,
        }

    def domain_agent_report_projection(self, _actor: Actor, run_id, agent_code: str) -> dict[str, object]:
        return {
            "schema_version": "DomainAgentReportViewV1",
            "run_id": str(run_id),
            "agent_code": agent_code,
            "status": "AVAILABLE",
            "findings": [{"claim": "durable projected finding"}],
        }


class _Objects:
    def __init__(self, body: bytes, *, observed_sha: str | None = None, size: int | None = None) -> None:
        self.body = body
        self.observed_sha = observed_sha or hashlib.sha256(body).hexdigest()
        self.size = len(body) if size is None else size

    def head(self, _key: str) -> ObjectMetadata:
        return ObjectMetadata(
            sha256=self.observed_sha,
            size_bytes=self.size,
            mime_type="application/json",
            etag="test",
            metadata={},
        )

    def get_private(self, _key: str, *, max_bytes: int) -> bytes:
        assert max_bytes == 2_000_000
        return self.body


def test_agent_report_body_is_integrity_checked_and_returned_as_json_text() -> None:
    body = b'{"schema_version":"DomainAgentReportViewV1","finding":"traceable"}'
    response = get_agent_report(
        uuid4(),
        "user-evidence",
        Actor(uuid4(), "agent-report-reader"),
        cast(Any, _ReadModel(body)),
        cast(Any, _Objects(body)),
    )

    payload = json.loads(response.body)
    assert payload["format"] == "json" and payload["content"] == body.decode()
    assert response.headers["cache-control"] == "no-store"


def test_legacy_binary_domain_report_returns_a_readable_durable_projection() -> None:
    body = b"\x89PNG\r\n\x1a\nlegacy-report-source"
    response = get_agent_report(
        uuid4(),
        "user-evidence",
        Actor(uuid4(), "agent-report-reader"),
        cast(Any, _ReadModel(body, mime_type="image/png")),
        cast(Any, _Objects(body)),
    )

    payload = json.loads(response.body)
    assert payload["format"] == "json"
    assert payload["projection_status"] == "LEGACY_SOURCE_PROJECTED"
    assert json.loads(payload["content"])["findings"][0]["claim"] == "durable projected finding"


def test_agent_report_rejects_sha_mismatch_before_reading_body() -> None:
    body = b'{"finding":"traceable"}'
    with pytest.raises(ArtifactIntegrityError, match="durable catalog"):
        get_agent_report(
            uuid4(),
            "user-evidence",
            Actor(uuid4(), "agent-report-reader"),
            cast(Any, _ReadModel(body)),
            cast(Any, _Objects(body, observed_sha="f" * 64)),
        )


def test_agent_report_rejects_objects_over_two_megabytes() -> None:
    body = b"bounded"
    with pytest.raises(ReportTooLargeError, match="2 MB"):
        get_agent_report(
            uuid4(),
            "evidence-auditor",
            Actor(uuid4(), "agent-report-reader"),
            cast(Any, _ReadModel(body)),
            cast(Any, _Objects(body, size=2_000_001)),
        )
