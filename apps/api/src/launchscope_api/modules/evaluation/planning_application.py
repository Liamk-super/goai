"""Validate a dynamic DAG node immediately before dispatch.

The application returns a decision only.  Leasing or changing a Task remains a
separate, authenticated control-plane operation after this gate has accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from launchscope_domain import EvaluationRun, Task
from launchscope_orchestrator.manifest_loader import AgentIdentityContract
from launchscope_skills import SkillContractError, SkillRegistry


@dataclass(frozen=True, slots=True)
class DispatchDecision:
    allowed: bool
    code: str = ""
    reason: str = ""


class DynamicDagDispatchValidator:
    """Fail closed before a Task is leased to an Agent/Worker."""

    def __init__(self, contracts: tuple[AgentIdentityContract, ...], skills: SkillRegistry | None = None) -> None:
        self.contracts = {contract.code: contract for contract in contracts}
        self.skills = skills or SkillRegistry()
        self.skills.load_p0()

    def validate(self, run: EvaluationRun, task: Task) -> DispatchDecision:
        manifest = run.manifest
        if manifest is None or not manifest.frozen:
            return DispatchDecision(False, "MANIFEST_NOT_FROZEN", "RunHarness must freeze the manifest before dispatch")
        if task.run_id != run.run_id:
            return DispatchDecision(False, "TASK_SCOPE_MISMATCH", "Task is outside the Run scope")
        try:
            run.task_dag().validate_for_dispatch(task.task_id)
        except Exception as exc:  # Domain errors deliberately carry safe rejection messages.
            return DispatchDecision(False, "DEPENDENCY_NOT_READY", str(exc))
        agent_code, agent_version = _agent_ref(task.agent_identity_ref)
        contract = self.contracts.get(agent_code)
        if contract is None or contract.version != agent_version:
            return DispatchDecision(False, "AGENT_IDENTITY_UNKNOWN", "Task Agent identity is not in the fixed team")
        expected_agent = manifest.agent_versions.get(agent_code)
        if expected_agent != f"{contract.version}:{contract.content_sha256}":
            return DispatchDecision(False, "AGENT_VERSION_MISMATCH", "Agent identity does not match frozen RunManifest")
        if not contract.permits_skill(task.skill_ref):
            return DispatchDecision(False, "SKILL_NOT_ALLOWED", "Agent identity does not allow this Skill")
        skill_hash = manifest.skill_versions.get(task.skill_ref)
        if skill_hash is None:
            return DispatchDecision(False, "SKILL_NOT_FROZEN", "Skill is absent from frozen RunManifest")
        try:
            skill = self.skills.resolve(task.skill_ref, task.skill_version, skill_hash)
        except SkillContractError as exc:
            return DispatchDecision(False, "SKILL_VERSION_MISMATCH", str(exc))
        if not contract.permits_tools(task.tool_allowlist):
            return DispatchDecision(False, "TOOL_NOT_ALLOWED", "Task tool allowlist exceeds Agent contract")
        if not set(task.tool_allowlist).issubset(manifest.tool_versions):
            return DispatchDecision(False, "TOOL_NOT_FROZEN", "Task tool allowlist exceeds frozen RunManifest")
        if not set(skill.document["permissions"]).issubset(manifest.permissions):
            return DispatchDecision(False, "PERMISSION_MISSING", "Skill permission is absent from frozen RunManifest")
        if not task.success_condition.strip():
            return DispatchDecision(False, "SUCCESS_CONDITION_MISSING", "Task requires an explicit success condition")
        expected_evidence = manifest.configuration.get("evidence_requirements", {})
        required_evidence = expected_evidence.get(task.skill_ref, ()) if isinstance(expected_evidence, Mapping) else ()
        if required_evidence and (not task.evidence_required or not task.evidence_requirement):
            return DispatchDecision(
                False,
                "EVIDENCE_REQUIREMENT_MISSING",
                "Task must carry its Skill's evidence requirement",
            )
        if task.budget_slice is None:
            return DispatchDecision(False, "BUDGET_SLICE_MISSING", "Task requires a budget slice before dispatch")
        reservation = next(
            (item for item in manifest.budget_limits if item.category == task.budget_slice.category),
            None,
        )
        if reservation is None or task.budget_slice.run_id != run.run_id:
            return DispatchDecision(False, "BUDGET_SCOPE_MISMATCH", "Task budget slice is not reserved by this Run")
        if task.budget_slice.reserved > reservation.remaining:
            return DispatchDecision(False, "BUDGET_EXCEEDED", "Task budget slice exceeds available frozen reservation")
        if task.timeout_seconds > manifest.timeout_seconds:
            return DispatchDecision(False, "TIMEOUT_EXCEEDED", "Task timeout exceeds the frozen Run timeout")
        return DispatchDecision(True)


def _agent_ref(value: str) -> tuple[str, str]:
    if "@" not in value:
        return value, ""
    return tuple(value.split("@", 1))  # type: ignore[return-value]


__all__ = ["DispatchDecision", "DynamicDagDispatchValidator"]
