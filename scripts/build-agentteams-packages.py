"""Validate the physical 1+4 CR bundle and build deterministic role-specific Worker packages."""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "packages" / "contracts" / "agents"
API_VERSION = "agentteams.io/v1beta1"
GENERATION = "v4"
OUTPUT_ROOT = ROOT / "infra" / "agentteams" / "generated" / f"packages-{GENERATION}"
CR_PATH = ROOT / "infra" / "agentteams" / "resources" / f"launchscope-team-{GENERATION}.yaml"


def load_documents() -> list[dict[str, Any]]:
    return [item for item in yaml.safe_load_all(CR_PATH.read_text(encoding="utf-8")) if item]


def load_contracts() -> dict[str, dict[str, Any]]:
    contract_root = CONTRACT_ROOT.parent / "manager" / "agents" if GENERATION in {"v5", "v6"} else CONTRACT_ROOT
    return {
        document["code"]: document
        for path in contract_root.glob(f"*.{GENERATION}.yaml")
        if (document := yaml.safe_load(path.read_text(encoding="utf-8")))
    }


def validate(documents: list[dict[str, Any]], contracts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if any(document.get("apiVersion") != API_VERSION for document in documents):
        raise ValueError(f"all AgentTeams resources must use {API_VERSION}")
    workers = [document for document in documents if document["kind"] == "Worker"]
    teams = [document for document in documents if document["kind"] == "Team"]
    humans = [document for document in documents if document["kind"] == "Human"]
    expected_workers = 5
    if (len(workers), len(teams), len(humans)) != (expected_workers, 1, 1):
        raise ValueError(
            f"the selected resource bundle requires exactly {expected_workers} Workers, 1 Team, and 1 Human"
        )
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
        if "browser-audit.v1" in allowed_tools:
            expected_mcp.add("browser-audit")
        if "public-research-search.v1" in allowed_tools:
            expected_mcp.add("public-research-search")
        if "user-validation-designer.start.v1" in allowed_tools:
            expected_mcp.add("user-validation-designer")
        if "user-validation-audit-context.get.v1" in allowed_tools:
            expected_mcp.add("user-validation-audit-context")
        if "material.read.v1" in allowed_tools:
            expected_mcp.add("material")
        comparable_mcp = configured_mcp
        if comparable_mcp != expected_mcp:
            raise ValueError(f"{code} MCP configuration drifted from its allowed tools")
    members = teams[0]["spec"]["workerMembers"]
    if len(members) != expected_workers or sum(member["role"] == "team_leader" for member in members) != 1:
        raise ValueError(f"Team must reference all {expected_workers} Workers with exactly one team_leader")
    if {member["name"] for member in members} != {worker["metadata"]["name"] for worker in workers}:
        raise ValueError("Team workerMembers must reference the independent Worker CRs exactly")
    expected_codes = {
        "evaluation-manager",
        "user-evidence",
        "product-engineering",
        "business-investment",
        "evidence-auditor",
    }
    if codes != expected_codes or teams[0]["spec"].get("peerMentions") is not False:
        raise ValueError(f"generation {GENERATION} requires the exact 1+4 topology with peer mentions disabled")
    return workers


def package_files(contract: dict[str, Any], worker_name: str | None = None) -> dict[str, str]:
    code = contract["code"]
    package_version = "6.0.14" if GENERATION == "v6" else contract["version"]
    context_version = "v2" if GENERATION in {"v5", "v6"} else "v1"
    role = code.upper().replace("-", "_")
    model = os.getenv(f"AGENTTEAMS_MODEL_{role}") or os.getenv("AGENTTEAMS_MODEL_ID", "qwen3.8-max")
    manifest = (
        json.dumps(
            {
                "version": package_version,
                "source": {
                    "hostname": "launchscope",
                    "os": "AgentTeams v1.2.0",
                    "created_at": "2026-08-06T00:00:00Z",
                },
                "worker": {
                    "suggested_name": worker_name or f"launchscope-{code}",
                    "model": model,
                    "runtime": "copaw",
                    "apt_packages": [],
                    "pip_packages": [],
                    "npm_packages": [],
                },
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    identity = (
        f"# IDENTITY\n\nAgent code: `{code}`\nVersion: `{contract['version']}`\n"
        f"Contract SHA-256: `{contract['content_sha256']}`\n"
    )
    soul = "# SOUL\n\nEvidence before assertion. Read-only by default. Fail closed on unknown side effects or cost.\n"
    agents = (
        "# AGENTS\n\n"
        + "\n".join(f"- {item}" for item in contract["responsibilities"])
        + f"\n\nRuntime MCP tools always include `launchscope-context.get.{context_version}`; "
        + "role-specific tools: "
        + json.dumps(contract["allowed_tools"])
        + "\n"
    )
    handoff_version = {"3.0": "v2", "4.0": "v3", "6.0": "v3"}.get(contract["schema_version"], "v1")
    handoff_skill_path = f"skills/launchscope-{code}-handoff-{handoff_version}"
    if GENERATION == "v6":
        context_call = (
            "Do not place context_token in a tool argument or shell command. Use the packaged local caller, which "
            "loads the latest authoritative assignment from the current Matrix channel using the Worker's own "
            "configured read credential: `python "
            f"{handoff_skill_path}/scripts/launchscope_mcp_call.py --server launchscope-context --tool "
            f"launchscope-context.get.{context_version} --args-json '{{}}'`. Use this same caller for every assigned "
            "MCP tool and pass only its non-token arguments through --args-json. Do not probe MCP tool names or read "
            "mcporter documentation first. After the context call, treat its top-level tool_allowlist as the current "
            "authoritative tool policy."
        )
    else:
        context_call = (
            "Use the exact CLI form `mcporter call --server launchscope-context --tool "
            f"launchscope-context.get.{context_version} --args "
            "'{\"context_token\":\"<exact context_token>\"}' --output json`. Do not probe MCP tool names or read "
            "mcporter documentation first."
        )
    skill = (
        f"# LaunchScope {code} handoff {handoff_version}\n\n"
        "The Human assignment is authoritative and contains tenant_id, run_id, task_id, agent_code, "
        f"context_token, handoff_schema, and usage_policy. First call `launchscope-context.get."
        f"{context_version}` with the "
        "assignment's exact context_token. Pass the same token to any other assigned MCP tool. Never copy "
        "routing values from prior chat messages.\n\n"
        f"{context_call} Make this context call once, then "
        "produce the assigned contract. Keep the final ReAct iteration for the required raw JSON object. Write a "
        "valid UUID literal yourself; do not call a tool only to generate one.\n\n"
        "Obey research_policy. When material_only is true, browser and public-search tools are optional: use the "
        "registered MATERIAL Evidence, complete the review, and mark claims that lack direct support as hypotheses. "
        "Do not block merely because an optional external research backend or URL is unavailable. When external "
        "research is enabled, use only authorized_urls returned by context/research_policy for browser audit; never "
        "guess a product domain.\n\n"
        "Return exactly one raw JSON object and no prose or Markdown. It must validate against the supplied "
        "handoff_schema. Copy message_type exactly from the current Human assignment, including a newer recovery "
        "message type when supplied. Copy tenant_id/run_id/task_id/agent_code exactly from this assignment. Link every "
        "non-hypothesis claim to Evidence IDs returned by MCP; never invent Evidence. If evidence is inadequate, "
        "mark the claim as a hypothesis or return status BLOCKED. On any refusal, tool error, policy conflict, or "
        "other failure, still return the same structured object with status BLOCKED or FAILED, an explicit "
        "failure_class and next_action, and empty claims/evidence where appropriate.\n\n"
        f"If clarification is required, use schema_version "
        f"{({'v1': '1.1', 'v2': '2.0', 'v3': '3.0'})[handoff_version]}, "
        "set status NEEDS_INPUT, include at least one "
        "information_requests item, and omit failure_class. For every other status, information_requests must be "
        "an empty list.\n\n"
        "Never fabricate provider_usage. Include it only when an actual immutable receipt is supplied by the "
        "runtime. When usage_policy.required is false, omit provider_usage and continue. Never write Run, Task, "
        "Decision, Report, or long-term memory state.\n"
    )
    if GENERATION == "v6" and code != "evaluation-manager":
        skill += (
            "\nBefore specialist analysis, read representative assigned content units from every required material "
            "in a single "
            "deterministic runtime step: `python "
            f"{handoff_skill_path}/scripts/launchscope_mcp_call.py --read-required-materials`. Use the returned "
            "context and material_reads as the current Task inputs, preserve every returned Evidence and "
            "source_locator, and do not replace this step with repeated reads of only the first material.\n"
            "\nReport citation rules: pass only the exact `source_locator` or `source_locators` objects returned "
            "by the current Task's MCP calls into the production report builder. Never turn a material-unit ref, "
            "object path, content ref, URL, or another Task's Evidence into a source_locator_id. Never add "
            "support_role to a source_directory entry; support_role belongs only to a citation. Run the packaged "
            "production builder before final handoff. The builder intentionally downgrades malformed or incomplete "
            "sources to PENDING_VALIDATION. For every required material_scope, call material.read.v1 for at least "
            "two assigned readable units when available, balanced within the eight-unit Task budget, and retain "
            "the exact "
            "returned Evidence and source_locator.\n"
            "\nAfter the production builder succeeds, parse its complete SpecialistReportDocumentV2 JSON output "
            "and include "
            "that object verbatim in the Matrix transport's top-level `specialist_report` field, alongside the "
            "schema-required `document` handoff. Do not leave the built report only in a local file, do not replace "
            "it with a screenshot or another Evidence ref, and do not put it inside `document`. The control plane "
            "will persist, SHA-bind, and adapt this inline report before accepting the handoff.\n"
        )
    if contract["schema_version"] in {"4.0", "6.0"}:
        skill += (
            "\nGeneration v4 rules: specialists execute the first round independently and must not dispatch or mention "
            "peer Workers. Copy dispatch_epoch from the Human assignment into the top-level response alongside the "
            "tenant/run/task routing fields. "
            "Return both an immutable readable report_ref and structured findings. Every finding "
            "must cite evidence, region_scope, as_of, valid_until, and report_section_ref. The evaluation manager "
            "may only plan, submit one controlled replan for unstarted tasks, and synthesize audited inputs. The "
            "evidence auditor runs only after domain tasks are terminal, never rewrites source findings, and may "
            "request at most one targeted remediation followed by at most one re-audit.\n"
        )
    if contract["schema_version"] == "6.0" and code == "evaluation-manager":
        skill += (
            "\nReport v2.2 rules: when the assignment requests ManagerPlanV2, return that planning transport and use "
            "the published ManagerPlanV2 score_profile_ref `score-profile:full-potential@1.0` for FULL_POTENTIAL. "
            "When the assignment "
            "requests ManagerSynthesisV2, produce one ManagerSynthesisV2 payload inside the required handoff JSON "
            "object and reuse the exact Claim "
            "and Citation "
            "identifiers supplied by the immutable synthesis context. Copy dispatch_epoch from the Human assignment "
            "into the top-level response alongside the routing fields. Do not supply or alter the "
            "potential index, dimension scores, Evidence coverage, confidence, comparison, or recommendation. "
            "A VERIFIED Claim requires a SUPPORT Citation whose audit_status is VERIFIED. A DOWNGRADED Claim "
            "requires a SUPPORT Citation whose audit_status is VERIFIED or DOWNGRADED. Otherwise use "
            "PENDING_VALIDATION with score_bearing false; BACKGROUND Citations never establish Claim strength. "
            "Do not introduce market, legal, competitor, or financial facts absent from audited context. If a "
            "renderer or validator fails, return the structured failure contract; never construct a manual JSON "
            "fallback from memory.\n"
        )
    files = {
        "manifest.json": manifest,
        "config/IDENTITY.md": identity,
        "config/SOUL.md": soul,
        "config/AGENTS.md": agents,
        f"{handoff_skill_path}/SKILL.md": skill,
    }
    if GENERATION == "v6":
        helper = ROOT / "scripts" / "agentteams" / "launchscope_mcp_call.py"
        files[f"{handoff_skill_path}/scripts/launchscope_mcp_call.py"] = helper.read_text(encoding="utf-8")
    if code != "evaluation-manager":
        for alias in contract.get("allowed_skills", []):
            files[f"skills/{alias}/SKILL.md"] = skill
    report_v22_runtimes = {
        "user-evidence": "user-validation-designer",
        "product-engineering": "product-technical-audit",
        "business-investment": "business-investment-assessment",
        "evidence-auditor": "evidence-grounding-audit",
    }
    if GENERATION == "v6" and code in report_v22_runtimes:
        skill_code = report_v22_runtimes[code]
        runtime_root = ROOT / "packages" / skill_code
        include_roots = ("knowledge", "prompts", "runner", "schema", "src")
        runtime_paths = [runtime_root / "SKILL.md", runtime_root / "package.json"]
        for name in include_roots:
            root = runtime_root / name
            if root.is_dir():
                runtime_paths.extend(path for path in root.rglob("*") if path.is_file())
        for path in runtime_paths:
            relative = path.relative_to(runtime_root).as_posix()
            files[f"skills/{skill_code}/{relative}"] = path.read_text(encoding="utf-8")
        source_normalizer = ROOT / "packages" / "_shared" / "report-source-normalization.mjs"
        files["skills/_shared/report-source-normalization.mjs"] = source_normalizer.read_text(encoding="utf-8")
        role_detail = {
            "user-evidence": (
                "The same User Agent produces every user-validation step; no second model lane is allowed."
            ),
            "product-engineering": (
                "Apply executable stage gates and treat team design statements as pending until code or runtime "
                "evidence verifies them."
            ),
            "business-investment": (
                "Separate facts, assumptions, and ranges; market, competition, and legal claims require region and "
                "time source metadata."
            ),
            "evidence-auditor": (
                "Keep raw audit codes in structured details and use the supplied user-facing label map for default "
                "presentation."
            ),
        }[code]
        files[f"skills/{skill_code}/LAUNCHSCOPE.md"] = (
            "# LaunchScope report v2.2 runtime binding\n\n"
            "Produce exactly one immutable SpecialistReportDocumentV2 from the assigned material scope and supplied "
            "Evidence/SourceLocator identities. Summary, full-page, and PDF views must select this same source SHA. "
            "Do not embed an iframe or treat HTML as canonical truth. Unsupported claims are PENDING_VALIDATION and "
            "not score-bearing. Return a structured failure after runtime or schema validation failure; manual JSON "
            "fallback is prohibited. Use only exact source_locator/source_locators objects returned by MCP and pass "
            f"them through the packaged production builder. {role_detail}\n"
        )
    elif code == "evidence-auditor":
        runtime_root = ROOT / "packages" / "evidence-grounding-audit"
        audit_include_roots = ("agents", "knowledge", "prompts", "runner", "schema", "src")
        runtime_paths = [runtime_root / "SKILL.md", runtime_root / "package.json"]
        for name in audit_include_roots:
            runtime_paths.extend(path for path in (runtime_root / name).rglob("*") if path.is_file())
        for path in runtime_paths:
            relative = path.relative_to(runtime_root).as_posix()
            files[f"skills/evidence-grounding-audit/{relative}"] = path.read_text(encoding="utf-8")
    if code == "user-evidence" and GENERATION != "v6":
        upstream_skill = ROOT / "packages" / "user-validation-designer" / "SKILL.md"
        files["skills/user-validation-designer/SKILL.md"] = upstream_skill.read_text(encoding="utf-8")
        files["skills/user-validation-designer/LAUNCHSCOPE.md"] = (
            "# LaunchScope runtime binding\n\n"
            "Use only the assigned user-validation-designer MCP tools. The same User Agent produces each requested "
            "step JSON; no second model lane is allowed. Submit at most two retries for the same step. Return only "
            "skill_result_ref, skill_result_sha256, validation_mode, evidence refs, claims and bounded summary in "
            "AgentHandoffV2. Never place the checkpoint, native report, prompt, knowledge text or model log in "
            "Matrix.\n"
        )
    if code == "evidence-auditor" and GENERATION != "v6":
        files["skills/evidence-grounding-audit/LAUNCHSCOPE.md"] = (
            "# User-validation audit binding\n\n"
            "Read the report only through user-validation-audit-context.get.v1. Cite exact KB-EVD rule ids and "
            "Evidence ids. Emit ACCEPTED, DOWNGRADED, NEEDS_MORE or REJECTED without rewriting the source report.\n"
        )
    return files


def build(workers: list[dict[str, Any]], contracts: dict[str, dict[str, Any]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for worker in workers:
        code = worker["metadata"]["annotations"]["launchscope.io/agent-code"]
        with zipfile.ZipFile(OUTPUT_ROOT / f"{code}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(package_files(contracts[code], str(worker["metadata"]["name"])).items()):
                info = zipfile.ZipInfo(name, (2026, 8, 6, 0, 0, 0))
                info.external_attr = 0o644 << 16
                archive.writestr(info, content.encode("utf-8"))


def main() -> None:
    global CR_PATH, GENERATION, OUTPUT_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="validate only; do not write generated ZIPs")
    parser.add_argument(
        "--generation",
        choices=("v4", "v5", "v6"),
        default=os.getenv("LAUNCHSCOPE_AGENT_GENERATION", "v4"),
    )
    args = parser.parse_args()
    GENERATION = args.generation
    OUTPUT_ROOT = ROOT / "infra" / "agentteams" / "generated" / f"packages-{GENERATION}"
    CR_PATH = ROOT / "infra" / "agentteams" / "resources" / f"launchscope-team-{GENERATION}.yaml"
    contracts = load_contracts()
    workers = validate(load_documents(), contracts)
    if not args.check:
        build(workers, contracts)
    print(f"validated AgentTeams {API_VERSION}: 1 Team, {len(workers)} Workers, 1 Human")


if __name__ == "__main__":
    main()
