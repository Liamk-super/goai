from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import yaml

from launchscope_orchestrator.manifest_loader import AgentManifestLoader

ROOT = Path(__file__).resolve().parents[3]
EXPECTED_CODES = {
    "evaluation-manager",
    "user-evidence",
    "product-engineering",
    "business-investment",
    "evidence-auditor",
}
DOMAIN_CODES = EXPECTED_CODES - {"evaluation-manager", "evidence-auditor"}


def _v4_documents() -> list[dict[str, object]]:
    path = ROOT / "infra/agentteams/resources/launchscope-team-v4.yaml"
    return [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if item]


def _v6_documents() -> list[dict[str, object]]:
    path = ROOT / "infra/agentteams/resources/launchscope-team-v6.yaml"
    return [item for item in yaml.safe_load_all(path.read_text(encoding="utf-8")) if item]


def test_generation_v4_bundle_has_exactly_five_workers_and_no_peer_mentions() -> None:
    documents = _v4_documents()
    workers = [item for item in documents if item["kind"] == "Worker"]
    team = next(item for item in documents if item["kind"] == "Team")
    codes = {item["metadata"]["annotations"]["launchscope.io/agent-code"] for item in workers}
    assert len(workers) == 5 and codes == EXPECTED_CODES
    assert len(team["spec"]["workerMembers"]) == 5
    assert team["spec"]["peerMentions"] is False
    assert team["metadata"]["name"] == "launchscope-potential-review-v4-operational"
    assert all("geo-policy-trend" not in item["metadata"]["name"] for item in workers)


def test_generation_v4_contracts_require_isolated_traceable_dual_outputs() -> None:
    for code in EXPECTED_CODES:
        contract = yaml.safe_load(
            (ROOT / f"packages/contracts/agents/{code}.v4.yaml").read_text(encoding="utf-8")
        )
        if code in DOMAIN_CODES:
            assert "first_round_isolated" in contract["risk_boundaries"]
            assert "no_peer_dispatch_or_free_mention" in contract["risk_boundaries"]
            assert contract["outputs"] == ["agent_handoff_v3", "domain_report_ref"]
            responsibility_text = " ".join(contract["responsibilities"])
            assert "region" in responsibility_text
            assert "freshness" in responsibility_text or "timing" in responsibility_text
        if code == "evidence-auditor":
            assert "serial_independent_gate" in contract["risk_boundaries"]
            assert "does_not_rewrite_findings_or_reports" in contract["risk_boundaries"]


def test_generation_v4_package_builder_is_flag_selected_and_bounded(monkeypatch) -> None:
    environment = {**os.environ, "LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED": "true"}
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/build-agentteams-packages.py"), "--check"],
        check=True,
        env=environment,
    )
    monkeypatch.setenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", "true")
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    contracts = namespace["load_contracts"]()
    workers = namespace["validate"](namespace["load_documents"](), contracts)
    assert len(workers) == 5 and set(contracts) == EXPECTED_CODES
    for code in DOMAIN_CODES:
        files = namespace["package_files"](contracts[code])
        skill = files[f"skills/launchscope-{code}-handoff-v3/SKILL.md"]
        assert "first round independently" in skill
        assert "region_scope, as_of, valid_until" in skill


def test_generation_v4_package_reserves_the_final_turn_after_one_exact_context_call() -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    contracts = namespace["load_contracts"]()
    files = namespace["package_files"](contracts["evaluation-manager"])
    skill = files["skills/launchscope-evaluation-manager-handoff-v3/SKILL.md"]
    assert "mcporter call --server launchscope-context --tool launchscope-context.get.v1" in skill
    assert "Do not probe MCP tool names or read mcporter documentation first" in skill
    assert "Keep the final ReAct iteration for the required raw JSON object" in skill
    assert "Write a valid UUID literal yourself; do not call a tool only to generate one" in skill


def test_default_bundle_is_v4_only_and_never_reintroduces_geo_worker(monkeypatch) -> None:
    monkeypatch.delenv("LAUNCHSCOPE_SUPERVISOR_1P4_ENABLED", raising=False)
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    contracts = namespace["load_contracts"]()
    workers = namespace["validate"](namespace["load_documents"](), contracts)
    assert len(workers) == 5 and set(contracts) == EXPECTED_CODES
    assert "geo-policy-trend" not in contracts


def test_generation_v6_manager_package_returns_only_bounded_synthesis_v2(monkeypatch) -> None:
    namespace = runpy.run_path(str(ROOT / "scripts/build-agentteams-packages.py"))
    monkeypatch.setitem(namespace, "GENERATION", "v6")
    contracts = {item.code: item.document for item in AgentManifestLoader().load_all("v6")}
    files = namespace["package_files"](contracts["evaluation-manager"])
    skill = files["skills/launchscope-evaluation-manager-handoff-v3/SKILL.md"]

    assert "produce one ManagerSynthesisV2 payload inside the required handoff JSON object" in skill
    assert "return only one ManagerSynthesisV2 JSON object" not in skill
    assert "Do not supply or alter the potential index" in skill
    assert "BACKGROUND Citations never establish Claim strength" in skill
    assert "never construct a manual JSON fallback" in skill


def test_generation_v6_bundle_uses_v6_workers_and_report_packages() -> None:
    documents = _v6_documents()
    workers = [item for item in documents if item["kind"] == "Worker"]
    team = next(item for item in documents if item["kind"] == "Team")

    assert len(workers) == 5
    assert {item["metadata"]["annotations"]["launchscope.io/agent-version"] for item in workers} == {"6.0"}
    assert all("packages-v6" in item["spec"]["package"] for item in workers)
    assert team["metadata"]["name"] == "launchscope-potential-review-v6-operational"
