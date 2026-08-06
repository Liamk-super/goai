"""Validate the 1+5 CR bundle and build deterministic role-specific Worker packages."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CR_PATH = ROOT / "infra" / "agentteams" / "resources" / "launchscope-team.yaml"
CONTRACT_ROOT = ROOT / "packages" / "contracts" / "agents"
OUTPUT_ROOT = ROOT / "infra" / "agentteams" / "generated" / "packages"
API_VERSION = "agentteams.io/v1beta1"


def load_documents() -> list[dict]:
    return [item for item in yaml.safe_load_all(CR_PATH.read_text(encoding="utf-8")) if item]


def load_contracts() -> dict[str, dict]:
    return {
        document["code"]: document
        for path in CONTRACT_ROOT.glob("*.v1.yaml")
        if (document := yaml.safe_load(path.read_text(encoding="utf-8")))
    }


def validate(documents: list[dict], contracts: dict[str, dict]) -> list[dict]:
    if any(document.get("apiVersion") != API_VERSION for document in documents):
        raise ValueError(f"all AgentTeams resources must use {API_VERSION}")
    workers = [document for document in documents if document["kind"] == "Worker"]
    teams = [document for document in documents if document["kind"] == "Team"]
    humans = [document for document in documents if document["kind"] == "Human"]
    if (len(workers), len(teams), len(humans)) != (6, 1, 1):
        raise ValueError("the resource bundle requires exactly 6 Workers, 1 Team, and 1 Human")
    codes = {worker["metadata"]["annotations"]["launchscope.io/agent-code"] for worker in workers}
    if codes != set(contracts):
        raise ValueError("Worker agent-code annotations drifted from packages/contracts/agents")
    for worker in workers:
        spec = worker["spec"]
        code = worker["metadata"]["annotations"]["launchscope.io/agent-code"]
        if spec.get("runtime") != "copaw" or not str(spec.get("model", "")).startswith("__LAUNCHSCOPE_MODEL_"):
            raise ValueError(f"{code} must use copaw and a rendered model placeholder")
        if code not in spec.get("package", ""):
            raise ValueError(f"{code} must use its role-specific package")
        configured_mcp = {item["name"] for item in spec.get("mcpServers", [])}
        allowed_tools = set(contracts[code]["allowed_tools"])
        expected_mcp = {"launchscope-context"}
        if "browser.read.v1" in allowed_tools:
            expected_mcp.add("browser-audit")
        if "public-research.get.v1" in allowed_tools:
            expected_mcp.add("public-research-search")
        if configured_mcp != expected_mcp:
            raise ValueError(f"{code} MCP configuration drifted from its allowed tools")
    members = teams[0]["spec"]["workerMembers"]
    if len(members) != 6 or sum(member["role"] == "team_leader" for member in members) != 1:
        raise ValueError("Team must reference all 6 Workers with exactly one team_leader")
    if {member["name"] for member in members} != {worker["metadata"]["name"] for worker in workers}:
        raise ValueError("Team workerMembers must reference the independent Worker CRs exactly")
    return workers


def package_files(contract: dict) -> dict[str, str]:
    code = contract["code"]
    identity = (
        f"# IDENTITY\n\nAgent code: `{code}`\nVersion: `{contract['version']}`\n"
        f"Contract SHA-256: `{contract['content_sha256']}`\n"
    )
    soul = "# SOUL\n\nEvidence before assertion. Read-only by default. Fail closed on unknown side effects or cost.\n"
    agents = (
        "# AGENTS\n\n"
        + "\n".join(f"- {item}" for item in contract["responsibilities"])
        + "\n\nRuntime MCP tools always include `launchscope-context.get.v1`; role-specific tools: "
        + json.dumps(contract["allowed_tools"])
        + "\n"
    )
    skill = (
        f"# LaunchScope {code} handoff v1\n\n"
        "The Human assignment is authoritative and contains tenant_id, run_id, task_id, agent_code, "
        "context_token, handoff_schema, and usage_policy. First call `launchscope-context.get.v1` with the "
        "assignment's exact context_token. Pass the same token to any other assigned MCP tool. Never copy "
        "routing values from prior chat messages.\n\n"
        "Obey research_policy. When material_only is true, browser and public-search tools are optional: use the "
        "registered MATERIAL Evidence, complete the review, and mark claims that lack direct support as hypotheses. "
        "Do not block merely because an optional external research backend or URL is unavailable.\n\n"
        "Return exactly one raw JSON object and no prose or Markdown. It must validate against the supplied "
        "handoff_schema. Copy tenant_id/run_id/task_id/agent_code exactly from this assignment. Link every "
        "non-hypothesis claim to Evidence IDs returned by MCP; never invent Evidence. If evidence is inadequate, "
        "mark the claim as a hypothesis or return status BLOCKED. On any refusal, tool error, policy conflict, or "
        "other failure, still return the same structured object with status BLOCKED or FAILED, an explicit "
        "failure_class and next_action, and empty claims/evidence where appropriate.\n\n"
        "Never fabricate provider_usage. Include it only when an actual immutable receipt is supplied by the "
        "runtime. When usage_policy.required is false, omit provider_usage and continue. Never write Run, Task, "
        "Decision, Report, or long-term memory state.\n"
    )
    return {
        "IDENTITY.md": identity,
        "SOUL.md": soul,
        "AGENTS.md": agents,
        f"skills/launchscope-{code}-handoff-v1/SKILL.md": skill,
    }


def build(workers: list[dict], contracts: dict[str, dict]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for worker in workers:
        code = worker["metadata"]["annotations"]["launchscope.io/agent-code"]
        with zipfile.ZipFile(OUTPUT_ROOT / f"{code}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(package_files(contracts[code]).items()):
                info = zipfile.ZipInfo(name, (2026, 8, 6, 0, 0, 0))
                info.external_attr = 0o644 << 16
                archive.writestr(info, content.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate only; do not write generated ZIPs")
    args = parser.parse_args()
    contracts = load_contracts()
    workers = validate(load_documents(), contracts)
    if not args.check:
        build(workers, contracts)
    print(f"validated AgentTeams {API_VERSION}: 1 Team, {len(workers)} Workers, 1 Human")


if __name__ == "__main__":
    main()
