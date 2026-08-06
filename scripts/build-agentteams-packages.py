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
        if spec.get("runtime") != "qwenpaw" or not str(spec.get("model", "")).startswith("__LAUNCHSCOPE_MODEL_"):
            raise ValueError(f"{code} must use qwenpaw and a rendered model placeholder")
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
        + "\n\nAllowed tools: "
        + json.dumps(contract["allowed_tools"])
        + "\n"
    )
    skill = (
        f"# LaunchScope {code} handoff v1\n\n"
        "Return only AgentHandoffV1. Link every claim to Evidence IDs. "
        "Copy tenant_id/run_id/task_id only from the assignment. Never fabricate provider usage: "
        "AgentTeams/Higress must attach its immutable provider_usage receipt; absence freezes the Run. "
        "Never write Run, Task, Decision, Report, or long-term memory state.\n"
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
