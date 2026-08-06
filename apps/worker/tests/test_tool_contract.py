from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

from launchscope_domain import BudgetReservation, RunManifest
from launchscope_worker.runtime.lease import LeaseRegistry
from launchscope_worker.tool_gateway.contract import AdapterResult, ToolContractRegistry, ToolGateway, ToolGatewayError
from launchscope_worker.tools.repository_read import RepositoryReader

ROOT = Path(__file__).resolve().parents[3]


def _manifest(tool_id: str, permission: str) -> RunManifest:
    return RunManifest(
        tool_versions={tool_id: "1.0"},
        permissions=(permission,),
        budget_limits=(BudgetReservation(uuid4(), "tool", 1, 1),),
        timeout_seconds=120,
    ).freeze()


def test_tool_contract_examples_are_valid_and_loaded() -> None:
    for path in (ROOT / "packages" / "contracts" / "tools").glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(document)
        validator = Draft202012Validator(document, format_checker=FormatChecker())
        assert not list(validator.iter_errors(document["examples"][0]))
    assert ToolContractRegistry().load("browser.read.v1").permission == "browser.read"


def test_gateway_requires_frozen_harness_tool_permission_budget_and_timeout() -> None:
    registry = LeaseRegistry()
    gateway = ToolGateway(leases=registry)
    run_id, task_id = uuid4(), uuid4()
    lease = registry.acquire(task_id, "worker-1", ttl_seconds=60)
    manifest = _manifest("browser.read.v1", "browser.read")
    result = gateway.invoke(
        run_id=run_id,
        task_id=task_id,
        task_tools=("browser.read.v1",),
        task_timeout_seconds=60,
        task_budget=0,
        manifest=manifest,
        lease_token=lease.token,
        tool_id="browser.read.v1",
        idempotency_key="tool:1",
        parameters={"url": "https://example.com"},
        adapter=lambda parameters, contract: AdapterResult(
            {
                "url": "https://example.com",
                "fetched_at": "2026-08-05T00:00:00+00:00",
                "snapshot_sha256": "a" * 64,
                "summary": "read-only page",
            }
        ),
    )
    assert result.status == "SUCCEEDED"
    try:
        gateway.invoke(
            run_id=run_id,
            task_id=task_id,
            task_tools=("browser.read.v1",),
            task_timeout_seconds=121,
            task_budget=0,
            manifest=manifest,
            lease_token=lease.token,
            tool_id="browser.read.v1",
            idempotency_key="tool:2",
            parameters={"url": "https://example.com"},
            adapter=lambda parameters, contract: AdapterResult({}),
        )
    except ToolGatewayError as error:
        assert "timeout" in str(error).lower()
    else:
        raise AssertionError("unfrozen timeout escalation must be rejected")


def test_real_repository_read_produces_evidence_and_tool_invocation(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("read-only evidence", encoding="utf-8")
    registry = LeaseRegistry()
    gateway = ToolGateway(leases=registry)
    run_id, task_id = uuid4(), uuid4()
    lease = registry.acquire(task_id, "worker-1", ttl_seconds=60)
    invocation = gateway.invoke(
        run_id=run_id,
        task_id=task_id,
        task_tools=("repository.read.v1",),
        task_timeout_seconds=30,
        task_budget=0,
        manifest=_manifest("repository.read.v1", "repository.read"),
        lease_token=lease.token,
        tool_id="repository.read.v1",
        idempotency_key="tool:repository:1",
        parameters={"path": "README.md"},
        adapter=RepositoryReader(tmp_path).read,
    )
    assert invocation.status == "SUCCEEDED"
    assert invocation.result["content"] == "read-only evidence"
    assert invocation.evidence is not None
    assert invocation.evidence["sha256"] == invocation.result["sha256"]
