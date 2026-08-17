"""Strict RocketMQ-to-AgentTeams and Matrix-to-control-plane bridge contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from launchscope_domain import (
    MAX_CLARIFICATION_QUESTION_CHARS,
    MAX_CLARIFICATION_REASON_CHARS,
    MAX_IMPACT_DIMENSION_CHARS,
    MAX_PROFILE_FIELD_CHARS,
)

from .manifest_loader import AGENT_CODES

_MAX_HANDOFF_V2_BYTES = 32_768


class BridgePolicyError(ValueError):
    """A transport message cannot be tied to a frozen Run/Task/Agent identity."""


class SupersededHandoffError(BridgePolicyError):
    """The message is well-formed but answers a dispatch that no longer applies.

    This is a benign race, not an Agent contract violation: a re-dispatch after a
    clarification legitimately leaves the previous round's reply in the room.
    It must be discarded and acknowledged, never converted into a synthetic
    VALIDATION failure, because a rejected synthetic failure would stall the
    Matrix cursor and block the legitimate current-epoch reply behind it.
    """


class ClaimV1(BaseModel):
    statement: str = Field(min_length=1, max_length=10_000)
    evidence_ids: list[UUID]
    hypothesis: bool
    region: str | None = Field(default=None, max_length=100)
    fetched_at: str | None = None
    valid_until: str | None = None
    trend_signal: Literal["FAVORABLE", "NEUTRAL", "ADVERSE", "UNKNOWN"] | None = None

    @field_validator("evidence_ids")
    @classmethod
    def evidence_required_for_facts(cls, value: list[UUID], info: ValidationInfo) -> list[UUID]:
        return value


class AuditResultV1(BaseModel):
    finding_id: UUID
    decision: Literal["ACCEPTED", "DOWNGRADED", "REJECTED", "NEEDS_MORE_EVIDENCE"]
    reason: str = Field(min_length=1, max_length=2000)


class AuditScoreComponentsV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_strength: float = Field(ge=0, le=40)
    source_reliability: float = Field(ge=0, le=20)
    freshness: float = Field(ge=0, le=20)
    reasoning_quality: float = Field(ge=0, le=20)
    total: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def total_matches_components(self) -> AuditScoreComponentsV2:
        expected = self.evidence_strength + self.source_reliability + self.freshness + self.reasoning_quality
        if abs(expected - self.total) > 0.001:
            raise ValueError("AuditResultV2 total must equal the four KB-EVD-G01 components")
        return self


class AuditResultV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: UUID
    decision: Literal["ACCEPTED", "DOWNGRADED", "REJECTED", "NEEDS_MORE"]
    reason: str = Field(min_length=1, max_length=2000)
    rule_ids: list[str] = Field(min_length=1)
    evidence_ids: list[UUID] = Field(default_factory=list)
    score_components: AuditScoreComponentsV2
    flags: list[Literal["SIMULATION_ONLY", "SELF_CLAIM", "EXPIRED", "UNTRACEABLE", "TAMPERED", "CONFLICT"]] = Field(
        default_factory=list
    )

    @field_validator("rule_ids")
    @classmethod
    def evidence_rule_ids(cls, value: list[str]) -> list[str]:
        if any(not item.startswith("KB-EVD-") for item in value):
            raise ValueError("AuditResultV2 rule_ids must use the KB-EVD namespace")
        if len(set(value)) != len(value):
            raise ValueError("AuditResultV2 rule_ids must be unique")
        return value


class InformationRequestV1(BaseModel):
    """One user-owned fact an Agent needs before it can finish (ADR 0004)."""

    field: str = Field(min_length=1, max_length=MAX_PROFILE_FIELD_CHARS, pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(min_length=1, max_length=MAX_CLARIFICATION_QUESTION_CHARS)
    why_blocked: str = Field(min_length=1, max_length=MAX_CLARIFICATION_REASON_CHARS)
    dimension: str = Field(min_length=1, max_length=MAX_IMPACT_DIMENSION_CHARS)


class AgentHandoffV1(BaseModel):
    schema_version: str = Field(pattern=r"^1\.[01]$")
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    # ADR 0004: a clarification re-dispatch delivers the same Task again.  The
    # Agent echoes the epoch it was dispatched with so a result produced before
    # the user answered cannot be mistaken for the current attempt.  Optional so
    # a legacy 1.0 producer stays valid; the control plane decides how to treat
    # an absent echo.
    dispatch_epoch: int | None = Field(default=None, ge=0)
    agent_code: str
    status: Literal["SUCCEEDED", "BLOCKED", "FAILED", "NEEDS_INPUT"]
    dimension: str
    claims: list[ClaimV1]
    evidence_refs: list[UUID]
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    confidence: float = Field(ge=0, le=1)
    needs_human_approval: bool
    failure_class: str | None = None
    next_action: str = Field(min_length=1, max_length=2000)
    audit_results: list[AuditResultV1] = Field(default_factory=list)
    information_requests: list[InformationRequestV1] = Field(default_factory=list)

    @field_validator("agent_code")
    @classmethod
    def fixed_agent(cls, value: str) -> str:
        if value not in AGENT_CODES:
            raise ValueError("agent_code is not in the frozen 1+5 catalog")
        return value

    def model_post_init(self, __context: object) -> None:
        for claim in self.claims:
            if not claim.hypothesis and not claim.evidence_ids:
                raise ValueError("non-hypothesis Claims require Evidence IDs")
        referenced = {value for claim in self.claims for value in claim.evidence_ids}
        if not referenced.issubset(set(self.evidence_refs)):
            raise ValueError("Claim evidence_ids must be present in evidence_refs")
        if self.status == "NEEDS_INPUT":
            if self.schema_version != "1.1":
                raise ValueError("NEEDS_INPUT requires AgentHandoff schema_version 1.1")
            if not self.information_requests:
                raise ValueError("NEEDS_INPUT requires at least one information_request")
            if self.failure_class is not None:
                raise ValueError("NEEDS_INPUT is a clarification, not a failure; omit failure_class")
            fields = [item.field for item in self.information_requests]
            if len(set(fields)) != len(fields):
                raise ValueError("information_requests must not repeat the same field")
        elif self.information_requests:
            raise ValueError("information_requests are only valid when status is NEEDS_INPUT")


class AgentHandoffV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"]
    tenant_id: UUID
    run_id: UUID
    task_id: UUID
    dispatch_epoch: int | None = Field(default=None, ge=0)
    agent_code: str
    status: Literal["SUCCEEDED", "BLOCKED", "FAILED", "NEEDS_INPUT"]
    dimension: str
    claims: list[ClaimV1] = Field(max_length=20)
    evidence_refs: list[UUID] = Field(max_length=100)
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    confidence: float = Field(ge=0, le=1)
    needs_human_approval: bool
    failure_class: str | None = None
    next_action: str = Field(min_length=1, max_length=2000)
    audit_results: list[AuditResultV2] = Field(default_factory=list, max_length=100)
    information_requests: list[InformationRequestV1] = Field(default_factory=list)
    skill_result_ref: UUID | None = None
    skill_result_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    validation_mode: Literal["first_validation", "version_regression", "evidence_recheck"] | None = None

    @field_validator("agent_code")
    @classmethod
    def fixed_agent(cls, value: str) -> str:
        if value not in AGENT_CODES:
            raise ValueError("agent_code is not in the frozen 1+5 catalog")
        return value

    def model_post_init(self, __context: object) -> None:
        for claim in self.claims:
            if not claim.hypothesis and not claim.evidence_ids:
                raise ValueError("non-hypothesis Claims require Evidence IDs")
        referenced = {value for claim in self.claims for value in claim.evidence_ids}
        if not referenced.issubset(set(self.evidence_refs)):
            raise ValueError("Claim evidence_ids must be present in evidence_refs")
        if self.status == "NEEDS_INPUT":
            if not self.information_requests:
                raise ValueError("NEEDS_INPUT requires at least one information_request")
            if self.failure_class is not None:
                raise ValueError("NEEDS_INPUT is a clarification, not a failure; omit failure_class")
        elif self.information_requests:
            raise ValueError("information_requests are only valid when status is NEEDS_INPUT")
        result_fields = (self.skill_result_ref, self.skill_result_sha256, self.validation_mode)
        if any(value is not None for value in result_fields) and not all(value is not None for value in result_fields):
            raise ValueError("Skill result ref, sha256 and validation_mode must be supplied together")
        if (
            self.agent_code == "user-evidence"
            and self.status == "SUCCEEDED"
            and not all(value is not None for value in result_fields)
        ):
            raise ValueError("a successful V2 User handoff requires an integrity-bound Skill result")
        if self.agent_code != "evidence-auditor" and self.audit_results:
            raise ValueError("only evidence-auditor may emit AuditResultV2")


class MatrixSenderDirectory(Protocol):
    def agent_for_mxid(self, mxid: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ManagerAssignment:
    run_id: UUID
    team_name: str
    body: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AcceptedMatrixEvent:
    matrix_event_id: str
    room_id: str
    sender_mxid: str
    payload_sha256: str
    handoff: AgentHandoffV1 | AgentHandoffV2


class AgentTeamsBridge:
    TEAM_NAME = "launchscope-potential-review"

    def assignment_from_dispatch(self, event: Mapping[str, object]) -> ManagerAssignment:
        if event.get("event_type") != "evaluation.task.ready.v1":
            raise BridgePolicyError("Bridge accepts only the versioned Task-ready event")
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or payload.get("team_name") != self.TEAM_NAME:
            raise BridgePolicyError("dispatch event does not target the frozen LaunchScope Team")
        run_id = UUID(str(event.get("run_id")))
        required = (
            "agent_code",
            "stage_code",
            "skill_ref",
            "context_token",
            "handoff_schema",
            "usage_policy",
            "research_policy",
        )
        if not event.get("tenant_id") or not event.get("task_id") or any(not payload.get(key) for key in required):
            raise BridgePolicyError("task assignment lacks routing, schema, capability, or policy")
        agent_code = str(payload.get("agent_code"))
        stage_code = str(payload.get("stage_code"))
        agent_contract_generation = str(payload.get("agent_contract_generation") or "")
        message_type = str(payload.get("message_type") or "")
        if message_type:
            if message_type not in {
                "ManagerPlanV1",
                "ManagerPlanV2",
                "AgentHandoffV3",
                "AgentHandoffV4",
                "AuditResultV3",
                "AuditResultV4",
                "ManagerSynthesisV1",
                "ManagerSynthesisV2",
            }:
                raise BridgePolicyError("task assignment names an unsupported generation-v4 message type")
            transport_key = "documents" if message_type in {"AuditResultV3", "AuditResultV4"} else "document"
            stage_instructions = {
                "ManagerPlanV1": (
                    "Call launchscope-context.get.v1 exactly once with context_token using `mcporter call --server "
                    "launchscope-context --tool launchscope-context.get.v1 --args "
                    "'{\"context_token\":\"<exact assignment context_token>\"}' --output json`. "
                    "Use requirement_brief and "
                    "planning_constraints from that response. Create a plan with new UUID values. For FULL_POTENTIAL "
                    'use exactly three Tasks, input_refs ["requirement-brief:current"], no more than two short '
                    "analysis_dimensions and one short success_condition per Task. Set deadline_suggestion_seconds "
                    "to 900 and every Task deadline_seconds to 900. Copy each tool_policy exactly "
                    "from planning_constraints.allowed_tools_by_agent; never invent or rename a tool. Do not run "
                    "agt, inspect shared files, message Workers, or perform domain research. After the context MCP "
                    "result, immediately return the raw JSON transport. Never call write_file or use shell commands "
                    "to draft, validate, measure, print, move, or delete the plan."
                ),
                "ManagerPlanV2": (
                    "Call launchscope-context.get.v2 exactly once with context_token using `mcporter call --server "
                    "launchscope-context --tool launchscope-context.get.v2 --args "
                    "'{\"context_token\":\"<exact assignment context_token>\"}' --output json`. "
                    "Use requirement_brief, "
                    "planning_constraints, and the compact material_catalog. Create exactly one bounded "
                    "material_scope per relevant material, using only catalog unit_ref values and explaining why "
                    "each scope is assigned. Every Task must contain at least one material_scope; an empty array "
                    "violates ManagerPlanV2. Set deadline_suggestion_seconds to 3600 and every Task "
                    "deadline_seconds to 3600. Never read raw attachments or invent unit refs. Copy each tool_policy "
                    "exactly from planning_constraints.allowed_tools_by_agent. After that single context result, "
                    "immediately return the raw JSON transport. Never call write_file or use shell commands to "
                    "draft, validate, measure, print, move, or delete the plan. Do not perform domain research or "
                    "coordinate Workers."
                ),
                "AgentHandoffV3": (
                    "Call the launchscope-context tool listed in tool_allowlist first, then use only permitted MCP "
                    "tools. If material.read.v1 is allowed, read only material_scope unit refs and cite the returned "
                    "immutable ref and sha256. "
                    "Make at most two browser/search calls total, then stop researching. Use no more "
                    "than one returned content_ref value total. Copy the exact same complete ref/sha256 pair into "
                    "document.evidence_refs, and make every finding.evidence_refs value a subset of those exact "
                    "top-level refs; never cite a ref omitted from document.evidence_refs. Use one of those refs as "
                    "report_ref. Return one compact, decision-relevant finding. Keep the claim under 300 UTF-8 "
                    "bytes, at most two short limitations, and next_action under 240 UTF-8 bytes. Every "
                    "non-hypothesis finding must cite the immutable ref. After the final MCP result, immediately "
                    "return the raw JSON transport in the assistant response. Never call write_file or use shell "
                    "commands to draft, validate, measure, print, move, or delete the handoff. Do not use built-in "
                    "web search, local browser automation, shared files, agt, or peer coordination."
                ),
                "AuditResultV3": (
                    "Call the launchscope-context tool listed in tool_allowlist exactly once. Return one audit "
                    "document for every "
                    "audit_identity_lock item in ordinal order. Copy finding_id and source_finding_sha256 only from "
                    "audit_identity_lock, character-for-character; never reconstruct, abbreviate, or alter either "
                    "identifier from audit_findings or memory. Do not rewrite findings, use shared files, run agt, "
                    "coordinate peers, call browser_use, install a browser, search, or perform new research. After "
                    "the context MCP result, immediately return the raw JSON transport. "
                    "Never call write_file or use shell commands to draft, validate, measure, print, move, or "
                    "delete the audit."
                ),
                "AuditResultV4": (
                    "Call the launchscope-context tool listed in tool_allowlist exactly once. Return one audit "
                    "document for every audit_identity_lock item in ordinal order. Every document must use "
                    "schema_version 4.0 and populate the citation admission fields from the immutable context. "
                    "Copy finding_id and source_finding_sha256 character-for-character from audit_identity_lock. "
                    "Build one complete evidence-auditor SpecialistReportDocumentV2 with the packaged "
                    "evidence-grounding-audit runtime; unsupported claims must be PENDING_VALIDATION and not "
                    "score-bearing. Do not rewrite findings, coordinate peers, or perform new research."
                ),
                "ManagerSynthesisV1": (
                    "Call launchscope-context.get.v1 exactly once. Use only synthesis_context, keep the deterministic "
                    "Decision unchanged, cite every accepted or downgraded Finding, and use the exact complete "
                    "immutable evidence_refs string from an audited Finding for every EVIDENCE citation ref; never "
                    "use an Evidence record UUID as the citation ref. Do not run agt, inspect "
                    "shared files, message Workers, or perform new research. After the context MCP result, "
                    "immediately return the raw JSON transport. Never call write_file or use shell commands to "
                    "draft, validate, measure, print, move, or delete the synthesis."
                ),
                "ManagerSynthesisV2": (
                    "Call launchscope-context.get.v2 exactly once. Use only synthesis_context and keep the "
                    "deterministic Decision unchanged. Return one valid ManagerSynthesisV2 using only Claim and "
                    "Citation identifiers supplied by the immutable context. Copy dispatch_epoch from the Human "
                    "assignment into the top-level response alongside the routing fields. Do not invent market, "
                    "competitor, "
                    "legal, user, or financial facts, and do not change scores, recommendation, confidence, "
                    "evidence coverage, or comparison. A VERIFIED Claim requires a SUPPORT Citation whose "
                    "audit_status is VERIFIED. A DOWNGRADED Claim requires a SUPPORT Citation whose audit_status "
                    "is VERIFIED or DOWNGRADED. Otherwise set the Claim to PENDING_VALIDATION and score_bearing "
                    "to false; BACKGROUND Citations never establish Claim strength. Return the raw JSON transport "
                    "immediately after synthesis."
                ),
            }
            stage_instructions["AgentHandoffV4"] = stage_instructions["AgentHandoffV3"]
            stage_instruction = stage_instructions[message_type]
            report_v2_specialist = agent_contract_generation == "v6" and message_type in {
                "AgentHandoffV3",
                "AgentHandoffV4",
                "AuditResultV4",
            }
            report_v2_domain = report_v2_specialist and message_type in {"AgentHandoffV3", "AgentHandoffV4"}
            if report_v2_domain:
                material_preflight = (
                    f"python skills/launchscope-{agent_code}-handoff-v3/scripts/launchscope_mcp_call.py "
                    "--read-required-materials"
                )
                report_runner = (
                    "skills/user-validation-designer/runner/report-cli.mjs"
                    if agent_code == "user-evidence"
                    else f"skills/{payload.get('skill_ref')}/runner/cli.mjs"
                )
                stage_instruction = stage_instruction.replace(
                    "Call the launchscope-context tool listed in tool_allowlist first, then use only permitted MCP "
                    "tools. If material.read.v1 is allowed, read only material_scope unit refs and cite the returned "
                    "immutable ref and sha256. ",
                    f"First run `{material_preflight}` exactly once. Do not transcribe context_token into a command "
                    "or tool argument. Use the returned context and material_reads as the authoritative Task inputs, "
                    "retain every returned immutable Evidence and source_locator, then use only permitted MCP tools. ",
                )
                stage_instruction = stage_instruction.replace(
                    "Never call write_file or use shell commands to draft, validate, measure, print, move, or "
                    "delete the handoff. ",
                    "Do not use shell or task-local files except to invoke the packaged report runtime. ",
                )
                stage_instruction += (
                    " This report-v2.2 Task must also build one complete SpecialistReportDocumentV2 with the "
                    f"packaged runtime `{report_runner}`. The preflight runtime_context is authoritative for "
                    "project_id, product_version_id, and product_title; copy those values exactly into an identity "
                    "object with a new UUID report_id and the assigned run_id. The runtime input must produce at "
                    "least eight claims across at least six named domain sections, including evidence-backed "
                    "progress and explicit PENDING_VALIDATION gaps. Use write_file exactly once for one uniquely "
                    "named task-local runtime input, then run the packaged Node report runner once with that file "
                    "as stdin and use its stdout byte-for-byte as specialist_report. Never run rm or mv, and leave "
                    "the task-local input in place. Do not add tenant_id, task_id, or as_of to specialist_report. "
                    "The runtime invocation and task-local input are the only permitted shell/file exception. "
                    "Do not create a manual fallback or manually alter runtime stdout. "
                    "Set document.report_ref to null; the control plane will persist and bind the outer "
                    "specialist_report after schema and identity validation."
                )
                stage_instruction += (
                    " The frozen output locale is runtime_context.report_preferences.locale. Generate all "
                    "user-visible prose in document and specialist_report exclusively in that locale; preserve "
                    "contract enums, identifiers, URLs, and evidence refs exactly as specified."
                )
            elif report_v2_specialist:
                stage_instruction += (
                    " The frozen output locale is report_preferences.locale from launchscope-context. Generate "
                    "all user-visible prose in every audit document and specialist_report exclusively in that "
                    "locale; preserve contract enums, identifiers, URLs, and evidence refs exactly as specified."
                )
            elif agent_contract_generation == "v6" and message_type == "ManagerSynthesisV2":
                stage_instruction += (
                    " The frozen output locale is report_preferences.locale from launchscope-context. Set the "
                    "ManagerSynthesisV2 locale to that exact value and generate all user-visible prose exclusively "
                    "in that locale; preserve contract enums, identifiers, URLs, and evidence refs exactly as "
                    "specified."
                )
            if message_type in {"AgentHandoffV3", "AgentHandoffV4"} and stage_code == "TARGETED_REMEDIATION":
                stage_instruction += (
                    " This is a targeted remediation. Emit one replacement Finding with a new UUID for finding_id. "
                    "Never reuse the source finding_id from input_refs; the control plane preserves that source as "
                    "the immutable supersedes target."
                )
            transport_example = (
                f'{{"message_type":"{message_type}",'
                f'"tenant_id":"{event.get("tenant_id")}",'
                f'"run_id":"{run_id}",'
                f'"task_id":"{event.get("task_id")}",'
                f'"agent_code":"{agent_code}",'
                f'"{transport_key}":'
                + (
                    "[<documents validating against handoff_schema>]"
                    if transport_key == "documents"
                    else "<document validating against handoff_schema>"
                )
                + (',"specialist_report":<SpecialistReportDocumentV2>' if report_v2_specialist else "")
                + "}"
            )
            max_transport_bytes = 180000 if agent_contract_generation == "v6" else 8000
            return ManagerAssignment(
                run_id=run_id,
                team_name=self.TEAM_NAME,
                body={
                    "schema_version": "4.0",
                    "tenant_id": str(event.get("tenant_id")),
                    "run_id": str(run_id),
                    "task_id": str(event.get("task_id")),
                    "dispatch_epoch": int(str(payload.get("dispatch_epoch") or 0)),
                    "team_name": self.TEAM_NAME,
                    "manifest_sha256": str(payload.get("manifest_sha256")),
                    "agent_code": agent_code,
                    "agent_contract_generation": agent_contract_generation,
                    "stage_code": stage_code,
                    "skill_ref": str(payload.get("skill_ref")),
                    "context_token": str(payload.get("context_token")),
                    "message_type": message_type,
                    "handoff_schema": payload.get("handoff_schema"),
                    "usage_policy": payload.get("usage_policy"),
                    "research_policy": payload.get("research_policy"),
                    "instruction": (
                        "This is one bounded LaunchScope generation-v4 control-plane Task. "
                        + (
                            "Use the exact packaged material preflight command stated below. "
                            if report_v2_domain
                            else "Use the exact mcporter command stated below with the configured "
                            "launchscope-context server. "
                        )
                        + "Do not list or probe MCP tools and do not read mcporter documentation first. Return "
                        "exactly one raw JSON "
                        f"transport object under {max_transport_bytes} UTF-8 bytes and no prose or Markdown. Use "
                        "compact strings and no "
                        "whitespace. Copy tenant_id, run_id, task_id, and agent_code character-for-character from "
                        "the assignment; never reconstruct, abbreviate, or remove UUID hyphens. Use these exact "
                        "outer routing fields: "
                        f"{transport_example} {stage_instruction}"
                    ),
                },
            )
        audit_instruction = (
            " For evidence-auditor, return exactly one audit_results item for every audit_findings item: "
            "empty evidence_ids means NEEDS_MORE; hypothesis with evidence means DOWNGRADED; "
            "non-hypothesis with evidence means ACCEPTED."
            if agent_code == "evidence-auditor"
            else ""
        )
        stage_instruction = {
            "LEADER_PLANNING": (
                " This is a LaunchScope control-plane gate, not an AgentTeams Project. Do not inspect shared files, "
                "read organization/project/team coordination skills, run agt, or message Workers. Call "
                "launchscope-context.get.v1 exactly once through the configured launchscope-context MCP server and "
                "mcporter Skill, then immediately return a SUCCEEDED AgentHandoffV1 with "
                "dimension LEADER_PLANNING, empty claims/evidence_refs/audit_results/information_requests, LOW risk, "
                "and next_action stating that the control plane may dispatch domain specialists. This terminal "
                "handoff is the only delegation trigger; do not delegate Workers yourself."
            ),
            "DOMAIN_REVIEW": (
                " This is one already-assigned specialist Task, not an AgentTeams Project. Do not inspect shared "
                "files, run agt, or coordinate other Workers. Read the mcporter Skill and use only its configured "
                "MCP routes: launchscope-context.launchscope-context.get.v1 first, then browser-audit.browser-audit.v1 "
                "and/or public-research-search.public-research-search.v1 as tool_allowlist permits. Never use "
                "browser_use, local Playwright, or built-in web search. Do not claim a capability is unavailable "
                "before mcporter reports its configured server unhealthy. If the registered Product Profile or "
                "Validation Script explicitly marks a fact as missing or undetermined and says that fact must be "
                "clarified before this Task can proceed, stop before browser/search and return NEEDS_INPUT with one "
                "necessary, answerable question; do not replace the missing fact with UNKNOWN and continue. Return "
                "the terminal handoff in this turn."
            ),
            "EVIDENCE_AUDIT": (
                " This is one already-assigned audit Task. Do not inspect shared files, run agt, or coordinate other "
                "Workers. Read the mcporter Skill, call launchscope-context.launchscope-context.get.v1 once, audit "
                "every supplied finding, and return the terminal handoff within this turn."
            ),
            "RULE_SYNTHESIS": (
                " This is the final LaunchScope synthesis gate, not an AgentTeams Project. Do not inspect shared "
                "files, run agt, or message Workers. Read the mcporter Skill, call "
                "launchscope-context.launchscope-context.get.v1 exactly once, then immediately "
                "return a SUCCEEDED AgentHandoffV1 with dimension RULE_SYNTHESIS and empty claims/evidence_refs/"
                "audit_results/information_requests. The control plane commits the final report after this handoff."
            ),
        }.get(stage_code, "")
        return ManagerAssignment(
            run_id=run_id,
            team_name=self.TEAM_NAME,
            body={
                "schema_version": "1.0",
                "tenant_id": str(event.get("tenant_id")),
                "run_id": str(run_id),
                "task_id": str(event.get("task_id")),
                "dispatch_epoch": int(str(payload.get("dispatch_epoch") or 0)),
                "team_name": self.TEAM_NAME,
                "manifest_sha256": str(payload.get("manifest_sha256")),
                "agent_code": agent_code,
                "stage_code": stage_code,
                "skill_ref": str(payload.get("skill_ref")),
                "context_token": str(payload.get("context_token")),
                "handoff_schema": payload.get("handoff_schema"),
                "usage_policy": payload.get("usage_policy"),
                "research_policy": payload.get("research_policy"),
                "instruction": (
                    "Use the task capability with the assigned read-only MCP tools. "
                    "If research_policy.material_only is true, do not require browser/search: use registered material "
                    "Evidence and mark unsupported assertions as hypotheses. Otherwise, browser audit may use only "
                    "research_policy.authorized_urls; never guess a product domain. Do not exceed "
                    "research_policy.browser_calls_per_task or research_policy.search_queries_per_task. Use browser "
                    "calls only for the highest-value authorized pages and finish from existing Evidence or search "
                    "results after the limit is reached. Return exactly one AgentHandoffV1 "
                    "JSON object, echoing dispatch_epoch unchanged, including structured BLOCKED/FAILED results. If "
                    "clarification is required, use schema_version 1.1, status NEEDS_INPUT, at least one "
                    "information_requests item, and no failure_class; for every other status, information_requests "
                    "must be empty." + stage_instruction + audit_instruction
                ),
            },
        )

    def accept_matrix_event(
        self,
        event: Mapping[str, object],
        directory: MatrixSenderDirectory,
        *,
        expected_run_id: UUID,
        expected_task_id: UUID,
        expected_dispatch_epoch: int | None = None,
    ) -> AcceptedMatrixEvent:
        event_id = str(event.get("event_id", ""))
        room_id = str(event.get("room_id", ""))
        sender = str(event.get("sender", ""))
        content = event.get("content")
        if not event_id or not room_id or not sender or not isinstance(content, Mapping):
            raise BridgePolicyError("Matrix event lacks immutable identity fields")
        agent_code = directory.agent_for_mxid(sender)
        if agent_code is None:
            raise BridgePolicyError("Matrix sender MXID is not a reconciled Agent identity")
        serialized = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        if content.get("schema_version") == "2.0" and len(serialized) > _MAX_HANDOFF_V2_BYTES:
            raise BridgePolicyError("AgentHandoffV2 exceeds the narrow Matrix transport budget")
        handoff = (
            AgentHandoffV2.model_validate(content)
            if content.get("schema_version") == "2.0"
            else AgentHandoffV1.model_validate(content)
        )
        if handoff.agent_code != agent_code:
            raise BridgePolicyError("Matrix sender MXID does not match AgentHandoffV1.agent_code")
        if handoff.run_id != expected_run_id or handoff.task_id != expected_task_id:
            raise BridgePolicyError("Matrix handoff Run/Task does not match the durable assignment")
        if expected_dispatch_epoch:
            # Past epoch 0 this Task has been re-dispatched at least once, so an
            # absent echo is indistinguishable from a result produced before the
            # user answered.  Omission must not be a way to skip the check; a
            # legacy producer is only tolerated while no supersession exists.
            if handoff.dispatch_epoch is None:
                raise SupersededHandoffError(
                    "Matrix handoff omits dispatch_epoch for a re-dispatched Task; "
                    "the producer must echo the epoch it was assigned"
                )
            if handoff.dispatch_epoch != expected_dispatch_epoch:
                raise SupersededHandoffError(
                    "Matrix handoff dispatch_epoch is stale; it answers a superseded dispatch of this Task"
                )
        elif (
            expected_dispatch_epoch is not None
            and handoff.dispatch_epoch is not None
            and handoff.dispatch_epoch != expected_dispatch_epoch
        ):
            raise SupersededHandoffError(
                "Matrix handoff dispatch_epoch is stale; it answers a superseded dispatch of this Task"
            )
        digest = hashlib.sha256(serialized).hexdigest()
        return AcceptedMatrixEvent(event_id, room_id, sender, digest, handoff)


__all__ = [
    "AcceptedMatrixEvent",
    "AgentHandoffV1",
    "AgentHandoffV2",
    "AgentTeamsBridge",
    "AuditResultV1",
    "AuditResultV2",
    "AuditScoreComponentsV2",
    "BridgePolicyError",
    "ClaimV1",
    "InformationRequestV1",
    "ManagerAssignment",
    "MatrixSenderDirectory",
    "SupersededHandoffError",
]
