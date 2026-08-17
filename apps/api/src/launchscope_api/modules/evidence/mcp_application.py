"""Authenticated read-only MCP capabilities with private durable Evidence output."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_task_ticket,
    decision,
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    finding_evidence,
    material,
    material_analysis,
    material_read_receipt,
    material_selection,
    material_selection_item,
    material_unit,
    product_profile,
    project,
    requirement_brief,
    run_manifest,
    skill_invocation,
    skill_result,
    skill_version,
    task,
    task_material_scope,
    tool_invocation,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.evaluation.execution_control import ExecutionControlApplication, assert_run_active
from launchscope_api.modules.evidence.source_locator import (
    SourceLocatorDraft,
    SourceLocatorRepository,
    browser_source_locator,
    internal_material_source_locator,
    search_source_locators,
    source_locator_view,
)
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope
from launchscope_worker.tools.public_research import PublicResearchPolicyError, validate_public_https_url

from .tool_limits import BROWSER_CALLS_PER_TASK, SEARCH_QUERIES_PER_TASK

MCP_CONTEXT_V2_RESPONSE_BUDGET = 40_000


class ExternalStateUnknown(RuntimeError):
    """The caller cannot safely determine whether a paid/read-only side effect completed."""


class BrowserCaptureFailed(RuntimeError):
    """The local read-only browser failed before an Evidence artifact existed."""


class MaterialScopeDenied(ValueError):
    """The requested material unit is outside the immutable Task scope."""


class MaterialIntegrityFailed(RuntimeError):
    """A stored material unit no longer matches its immutable digest."""


def _representative_material_catalog_rows(
    rows: list[dict[str, Any]],
    *,
    per_material_limit: int = 8,
    total_limit: int = 40,
) -> list[dict[str, Any]]:
    material_order: list[UUID] = []
    by_material: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        material_id = UUID(str(row["material_id"]))
        if material_id not in by_material:
            material_order.append(material_id)
            by_material[material_id] = []
        by_material[material_id].append(row)
    selected: list[dict[str, Any]] = []
    for material_id in material_order:
        material_rows = by_material[material_id]
        readable = [
            row
            for row in material_rows
            if str(row["unit_type"]) not in {"DOCUMENT", "SECTION", "IMAGE"}
            and bool(str(row["summary"]).strip())
        ]
        candidates = readable or material_rows
        if len(candidates) > per_material_limit:
            candidates = [
                candidates[round(index * (len(candidates) - 1) / (per_material_limit - 1))]
                for index in range(per_material_limit)
            ]
        selected.extend(candidates)
        if len(selected) >= total_limit:
            return selected[:total_limit]
    return selected


def _bounded_material_context(
    base: dict[str, object],
    catalog_rows: list[dict[str, Any]],
    scope_views: list[dict[str, Any]],
    *,
    include_unscoped_catalog: bool = False,
) -> dict[str, object]:
    catalog_by_ref = {
        f"material-unit:{row['id']}@{row['sha256']}": row
        for row in catalog_rows
    }
    projected_scopes = []
    for row in scope_views:
        raw_refs = [str(value) for value in row["unit_refs"]]
        readable_refs = [
            value
            for value in raw_refs
            if value in catalog_by_ref
            and str(catalog_by_ref[value]["unit_type"]) not in {"DOCUMENT", "SECTION", "IMAGE"}
            and bool(str(catalog_by_ref[value]["summary"]).strip())
        ]
        candidates = readable_refs or [value for value in raw_refs if value in catalog_by_ref] or raw_refs
        if len(candidates) > 8:
            candidates = [candidates[round(index * (len(candidates) - 1) / 7)] for index in range(8)]
        projected_scopes.append({
            "scope_id": str(row["id"]) if row["id"] else None,
            "material_id": str(row["material_id"]) if row["material_id"] else None,
            "unit_refs": candidates,
            "reason": row["reason"],
            "required": row["required"],
            "scope_sha256": row["scope_sha256"],
        })
    assigned_refs = {value for scope in projected_scopes for value in scope["unit_refs"]}
    projected_catalog = []
    for row in catalog_rows:
        unit_ref = f"material-unit:{row['id']}@{row['sha256']}"
        if not include_unscoped_catalog and unit_ref not in assigned_refs:
            continue
        coverage = row["coverage"] if isinstance(row["coverage"], dict) else {}
        projected_catalog.append(
            {
                "material_id": str(row["material_id"]),
                "file_name": row["display_name"],
                "unit_ref": unit_ref,
                "parent_unit_id": str(row["parent_unit_id"]) if row["parent_unit_id"] else None,
                "unit_type": row["unit_type"],
                "locator": row["locator"],
                "tags": row["tags"],
                "confidence": float(row["confidence"]),
                "summary": str(row["summary"])[:240],
                "coverage_gaps": list(coverage.get("uncovered_locators", []))[:8],
            }
        )
    result = dict(base)
    result["material_catalog"] = projected_catalog
    result["material_scope"] = projected_scopes
    if len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")) > MCP_CONTEXT_V2_RESPONSE_BUDGET:
        for item in projected_catalog:
            item["summary"] = str(item["summary"])[:80]
            item["coverage_gaps"] = []
    if len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")) > MCP_CONTEXT_V2_RESPONSE_BUDGET:
        if include_unscoped_catalog:
            while (
                len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8"))
                > MCP_CONTEXT_V2_RESPONSE_BUDGET
                and len(projected_catalog) > 1
            ):
                projected_catalog.pop()
        else:
            for scope in projected_scopes:
                scope["unit_refs"] = scope["unit_refs"][:1]
            retained_refs = {value for scope in projected_scopes for value in scope["unit_refs"]}
            result["material_catalog"] = [item for item in projected_catalog if item["unit_ref"] in retained_refs]
    if len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")) > MCP_CONTEXT_V2_RESPONSE_BUDGET:
        raw_refs = result.get("evidence_refs")
        if isinstance(raw_refs, list):
            result["evidence_refs"] = [
                {**item, "summary": str(item.get("summary", ""))[:200]}
                for item in raw_refs
                if isinstance(item, dict)
            ]
    while len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")) > MCP_CONTEXT_V2_RESPONSE_BUDGET:
        bounded_refs = result.get("evidence_refs")
        if not isinstance(bounded_refs, list) or len(bounded_refs) <= 8:
            break
        result["evidence_refs"] = bounded_refs[: max(8, len(bounded_refs) // 2)]
    if len(json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")) > MCP_CONTEXT_V2_RESPONSE_BUDGET:
        raise ValueError("material context exceeds the MCP response budget")
    return result


@dataclass(frozen=True, slots=True)
class BrowserArtifact:
    final_url: str
    title: str
    fetched_at: datetime
    dom_summary: str
    screenshot: bytes
    region: str


@dataclass(frozen=True, slots=True)
class SearchArtifact:
    query: str
    region: str
    fetched_at: datetime
    results: tuple[dict[str, object], ...]
    usage_known: bool = True
    submission_known: bool = True


class BrowserAdapter(Protocol):
    def capture(self, url: str, *, timeout_seconds: int) -> BrowserArtifact: ...


class SearchAdapter(Protocol):
    def search(self, query: str, *, region: str, max_results: int, days: int | None) -> SearchArtifact: ...


class PlaywrightBrowserAdapter:
    def __init__(self, allowed_domains: tuple[str, ...]) -> None:
        self.allowed_domains = allowed_domains

    def capture(self, url: str, *, timeout_seconds: int) -> BrowserArtifact:
        allowed = validate_public_https_url(url, self.allowed_domains)
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 1000})
                    blocked: list[str] = []

                    def route_request(route: Any) -> None:
                        try:
                            validate_public_https_url(route.request.url, self.allowed_domains)
                        except PublicResearchPolicyError:
                            blocked.append(route.request.url[:500])
                            route.abort("blockedbyclient")
                        else:
                            route.continue_()

                    page.route("**/*", route_request)
                    page.goto(allowed, wait_until="commit", timeout=min(timeout_seconds, 30) * 1000)
                    page.locator("body").wait_for(state="attached", timeout=15_000)
                    with suppress(PlaywrightTimeoutError):
                        page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(1500)
                    if blocked:
                        raise PublicResearchPolicyError("page attempted a request outside the frozen domain allowlist")
                    final_url = validate_public_https_url(page.url, self.allowed_domains)
                    title = page.title()[:500]
                    summary = page.locator("body").inner_text(timeout=5000)[:4000]
                    screenshot = page.screenshot(full_page=True, type="png")
                    return BrowserArtifact(final_url, title, datetime.now(UTC), summary, screenshot, "GLOBAL")
                finally:
                    browser.close()
        except PublicResearchPolicyError:
            raise
        except Exception as exc:
            raise BrowserCaptureFailed("browser page did not become readable before capture") from exc


class TavilySearchAdapter:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is required for real public research search")

    def search(self, query: str, *, region: str, max_results: int, days: int | None) -> SearchArtifact:
        payload: dict[str, object] = {
            "api_key": self.api_key,
            "query": f"{query} region:{region}",
            "max_results": max_results,
            "search_depth": "basic",
            "include_raw_content": False,
        }
        if days is not None:
            payload["days"] = days
        request = urllib.request.Request(
            "https://api.tavily.com/search",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read(1_048_577))
        except Exception as exc:
            raise ExternalStateUnknown("search submission or billing state is unknown") from exc
        results = tuple(
            {
                "url": item.get("url"),
                "title": item.get("title"),
                "content": str(item.get("content", ""))[:2000],
                "score": item.get("score"),
                "published_date": item.get("published_date"),
            }
            for item in body.get("results", [])[:max_results]
        )
        return SearchArtifact(query, region, datetime.now(UTC), results)


class McpEvidenceApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: S3QuarantineObjectStore,
        *,
        browser: BrowserAdapter | None = None,
        search: SearchAdapter | None = None,
        allowed_browser_domains: tuple[str, ...] = (),
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._browser = browser or PlaywrightBrowserAdapter(allowed_browser_domains)
        self._search = search

    def context_get(self, actor: Actor, run_id: UUID, task_id: UUID) -> dict[str, object]:
        return self._context_get(actor, run_id, task_id, include_legacy_material_context=True)

    def _context_get(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        *,
        include_legacy_material_context: bool,
    ) -> dict[str, object]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assignment = self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            assigned_task = (
                session.execute(
                    select(task.c.stage_code, task.c.tool_allowlist).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.id == task_id,
                    )
                )
                .mappings()
                .one()
            )
            run = (
                session.execute(
                    select(
                        evaluation_run.c.project_id,
                        evaluation_run.c.product_version_id,
                        evaluation_run.c.standard_version,
                        run_manifest.c.frozen_config,
                        project.c.name.label("product_title"),
                    )
                    .join(
                        run_manifest,
                        run_manifest.c.run_id == evaluation_run.c.id,
                    )
                    .join(
                        project,
                        (project.c.tenant_id == evaluation_run.c.tenant_id)
                        & (project.c.id == evaluation_run.c.project_id),
                    )
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                )
                .mappings()
                .one()
            )
            profile = session.execute(
                select(product_profile.c.confirmed_fields)
                .where(
                    product_profile.c.tenant_id == actor.tenant_id,
                    product_profile.c.product_version_id == run["product_version_id"],
                    product_profile.c.confirmation_status == "CONFIRMED",
                )
                .order_by(product_profile.c.confirmed_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            refs = (
                session.execute(
                    select(
                        evidence.c.id,
                        evidence.c.source_type,
                        evidence.c.object_key,
                        evidence.c.sha256,
                        evidence.c.mime_type,
                        evidence.c.evidence_level,
                        evidence.c.trust_level,
                        evidence.c.summary,
                        evidence.c.simulated,
                        evidence.c.fetched_at,
                        evidence.c.valid_until,
                    )
                    .where(evidence.c.tenant_id == actor.tenant_id, evidence.c.run_id == run_id)
                    .limit(50)
                )
                .mappings()
                .all()
            )
            minimal_profile = (
                {str(key): profile[key] for key in sorted(profile)[:50]} if isinstance(profile, dict) else {}
            )
            brief = (
                session.execute(
                    select(requirement_brief.c.id, requirement_brief.c.document)
                    .where(
                        requirement_brief.c.tenant_id == actor.tenant_id,
                        requirement_brief.c.product_version_id == run["product_version_id"],
                        requirement_brief.c.status == "READY_FOR_PLANNING",
                    )
                    .order_by(requirement_brief.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            ticket = session.execute(
                select(agent_task_ticket.c.public_summary).where(
                    agent_task_ticket.c.tenant_id == actor.tenant_id,
                    agent_task_ticket.c.task_id == task_id,
                )
            ).scalar_one_or_none()
            current_tool_allowlist = [str(value) for value in assigned_task["tool_allowlist"] or []]
            ticket_view = dict(ticket) if isinstance(ticket, dict) else None
            if ticket_view is not None:
                ticket_view["tool_policy"] = current_tool_allowlist
            material_context: list[dict[str, object]] = []
            material_context_budget = 42_000 if include_legacy_material_context else 0
            for item in refs:
                if not include_legacy_material_context:
                    break
                if item["mime_type"] != "application/vnd.launchscope.material-analysis+json":
                    continue
                entry: dict[str, object] = {
                    "evidence_id": str(item["id"]),
                    "sha256": item["sha256"],
                    "status": "INTEGRITY_FAILED",
                }
                try:
                    body = self._objects.get_private(str(item["object_key"]), max_bytes=2_000_000)
                    if hashlib.sha256(body).hexdigest() != item["sha256"]:
                        material_context.append(entry)
                        continue
                    artifact = json.loads(body)
                    source = artifact.get("source") if isinstance(artifact, dict) else None
                    if not isinstance(source, dict):
                        material_context.append(entry)
                        continue
                    entry.update(
                        {
                            "status": "AVAILABLE",
                            "file_name": str(source.get("file_name") or "material")[:255],
                            "source_material_sha256": str(source.get("sha256") or "")[:64],
                            "page_count": int(artifact.get("page_count") or 0),
                            "context_pages": artifact.get("context_pages") or [],
                            "page_context": str(artifact.get("model_context") or "")[
                                : min(12_000, material_context_budget)
                            ],
                        }
                    )
                    material_context_budget -= len(str(entry["page_context"]))
                except (AttributeError, json.JSONDecodeError, TypeError, ValueError):
                    pass
                material_context.append(entry)
                if material_context_budget <= 0:
                    break
            audit_findings: list[dict[str, object]] = []
            user_validation_results: list[dict[str, object]] = []
            if assignment.startswith("evidence-auditor@"):
                for item in session.execute(
                    select(
                        finding.c.id,
                        finding.c.dimension_code,
                        finding.c.grade,
                        finding.c.statement,
                        finding.c.is_hypothesis,
                        finding.c.structured_result,
                    ).where(
                        finding.c.tenant_id == actor.tenant_id,
                        finding.c.run_id == run_id,
                    )
                ).mappings():
                    linked = (
                        session.execute(
                            select(finding_evidence.c.evidence_id).where(
                                finding_evidence.c.tenant_id == actor.tenant_id,
                                finding_evidence.c.finding_id == item["id"],
                            )
                        )
                        .scalars()
                        .all()
                    )
                    source = item["structured_result"]["finding"]
                    audit_findings.append(
                        {
                            "finding_id": str(item["id"]),
                            "dimension": item["dimension_code"],
                            "proposed_grade": item["grade"],
                            "statement": item["statement"][:2000],
                            "hypothesis": item["is_hypothesis"],
                            "evidence_ids": [str(value) for value in linked],
                            "source_finding_sha256": hashlib.sha256(
                                json.dumps(
                                    source,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    ensure_ascii=False,
                                ).encode("utf-8")
                            ).hexdigest(),
                            "finding": source,
                        }
                    )
                user_validation_results = [
                    {
                        "skill_result_ref": str(item["id"]),
                        "skill_result_sha256": item["sha256"],
                        "mode": item["mode"],
                        "status": item["status"],
                    }
                    for item in session.execute(
                        select(
                            skill_result.c.id,
                            skill_result.c.sha256,
                            skill_result.c.mode,
                            skill_result.c.status,
                        ).where(
                            skill_result.c.tenant_id == actor.tenant_id,
                            skill_result.c.run_id == run_id,
                        )
                    ).mappings()
                ]
            planning_constraints = None
            if assigned_task["stage_code"] == "LEADER_PLANNING" and brief is not None:
                material_v2 = run["frozen_config"].get("architecture_generation") in {
                    "supervisor-1p4-material-routing-v2",
                    "supervisor-1p4-report-v22",
                    "supervisor-1p4-report-v3",
                }
                context_tool = "launchscope-context.get.v2" if material_v2 else "launchscope-context.get.v1"
                material_tools = ["material.read.v1"] if material_v2 else []
                mode = str(brief["document"]["evaluation_mode"])
                profile_id = {
                    "FULL_POTENTIAL": "full-potential",
                    "INVESTMENT_REVIEW": "investment-review",
                    "LAUNCH_REVIEW": "launch-review",
                    "USER_VALIDATION": "user-validation",
                }[mode]
                planning_constraints = {
                    "evaluation_mode": mode,
                    "score_profile_ref": f"score-profile:{profile_id}@1.0",
                    "required_agents": (
                        ["user-evidence", "product-engineering", "business-investment"]
                        if mode == "FULL_POTENTIAL"
                        else []
                    ),
                    "allowed_tools_by_agent": {
                        "user-evidence": [
                            context_tool,
                            *material_tools,
                            "browser-audit.v1",
                            "public-research-search.v1",
                        ],
                        "product-engineering": [
                            context_tool,
                            *material_tools,
                            "browser-audit.v1",
                            "repository.read.v1",
                        ],
                        "business-investment": [
                            context_tool,
                            *material_tools,
                            "public-research-search.v1",
                        ],
                    },
                    "budget_cap_usd": 20,
                    "deadline_cap_seconds": 600,
                    "first_round_dependencies": [],
                }
            synthesis_context = None
            if assigned_task["stage_code"] == "SUPERVISOR_SYNTHESIS":
                decision_row = (
                    session.execute(
                        select(decision.c.id, decision.c.recommendation, decision.c.dimension_grades)
                        .where(decision.c.tenant_id == actor.tenant_id, decision.c.run_id == run_id)
                        .order_by(decision.c.created_at.desc())
                        .limit(1)
                    )
                    .mappings()
                    .one()
                )
                audited_rows = (
                    session.execute(
                        select(
                            finding.c.id,
                            finding.c.structured_result,
                            evidence_audit.c.decision.label("audit_decision"),
                        )
                        .join(
                            evidence_audit,
                            (evidence_audit.c.tenant_id == finding.c.tenant_id)
                            & (evidence_audit.c.finding_id == finding.c.id),
                        )
                        .where(finding.c.tenant_id == actor.tenant_id, finding.c.run_id == run_id)
                        .order_by(finding.c.id, evidence_audit.c.audit_round.desc())
                    )
                    .mappings()
                    .all()
                )
                latest: dict[str, dict[str, object]] = {}
                for item in audited_rows:
                    latest.setdefault(
                        str(item["id"]),
                        {
                            "finding_id": str(item["id"]),
                            "finding": item["structured_result"]["finding"],
                            "audit_decision": item["audit_decision"],
                        },
                    )
                synthesis_context = {
                    "decision_id": str(decision_row["id"]),
                    "deterministic_recommendation": decision_row["recommendation"],
                    "deterministic_score": decision_row["dimension_grades"],
                    "audited_findings": list(latest.values()),
                    "version_changes": {"improved": [], "unchanged": [], "new_risks": []},
                }
            result = {
                "tenant_id": str(actor.tenant_id),
                "run_id": str(run_id),
                "task_id": str(task_id),
                "project_id": str(run["project_id"]),
                "product_version_id": str(run["product_version_id"]),
                "product_title": str(run["product_title"]),
                "standard_version": run["standard_version"],
                "report_preferences": dict(run["frozen_config"].get("report_preferences") or {}),
                "product_profile": minimal_profile,
                "requirement_brief": None if brief is None else {"brief_id": str(brief["id"]), **brief["document"]},
                "assigned_task_ticket": ticket_view,
                "tool_allowlist": current_tool_allowlist,
                "planning_constraints": planning_constraints,
                "synthesis_context": synthesis_context,
                "authorized_urls": list(run["frozen_config"].get("research_targets", {}).get("authorized_urls", [])),
                "evidence_refs": [
                    {
                        "evidence_id": str(item["id"]),
                        "ref": item["object_key"],
                        "source_type": item["source_type"],
                        "sha256": item["sha256"],
                        "evidence_level": item["evidence_level"],
                        "trust_level": item["trust_level"],
                        "summary": item["summary"][:500],
                        "simulated": item["simulated"],
                        "fetched_at": item["fetched_at"].isoformat() if item["fetched_at"] else None,
                        "valid_until": item["valid_until"].isoformat() if item["valid_until"] else None,
                    }
                    for item in refs
                ],
                **({"material_context": material_context} if include_legacy_material_context else {}),
                "audit_findings": audit_findings,
                "audit_identity_lock": [
                    {
                        "ordinal": index + 1,
                        "finding_id": item["finding_id"],
                        "source_finding_sha256": item["source_finding_sha256"],
                    }
                    for index, item in enumerate(audit_findings)
                ],
                "user_validation_results": user_validation_results,
            }
            if include_legacy_material_context and len(json.dumps(result, default=str).encode("utf-8")) > 65_536:
                raise ValueError("minimal context exceeds the frozen MCP response budget")
            return result

    def context_get_v2(self, actor: Actor, run_id: UUID, task_id: UUID) -> dict[str, object]:
        result = self._context_get(actor, run_id, task_id, include_legacy_material_context=False)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assignment = self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            run = session.execute(
                select(evaluation_run.c.product_version_id).where(
                    evaluation_run.c.tenant_id == actor.tenant_id,
                    evaluation_run.c.id == run_id,
                )
            ).one()
            selection_id = session.execute(
                select(material_selection.c.id)
                .where(
                    material_selection.c.tenant_id == actor.tenant_id,
                    material_selection.c.product_version_id == run.product_version_id,
                )
                .order_by(material_selection.c.revision.desc())
                .limit(1)
            ).scalar_one_or_none()
            included_analysis_ids = (
                []
                if selection_id is None
                else list(
                    session.execute(
                        select(material_selection_item.c.analysis_id).where(
                            material_selection_item.c.tenant_id == actor.tenant_id,
                            material_selection_item.c.selection_id == selection_id,
                            material_selection_item.c.decision.in_(("INCLUDE", "INCLUDE_PARTIAL")),
                        )
                    ).scalars()
                )
            )
            scope_rows = (
                session.execute(
                    select(task_material_scope).where(
                        task_material_scope.c.tenant_id == actor.tenant_id,
                        task_material_scope.c.run_id == run_id,
                        task_material_scope.c.task_id == task_id,
                    )
                )
                .mappings()
                .all()
            )
            planning_catalog = session.execute(
                select(task.c.stage_code).where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run_id,
                    task.c.id == task_id,
                )
            ).scalar_one() == "LEADER_PLANNING"
            if assignment.startswith("evidence-auditor@"):
                cited_keys = list(
                    session.execute(
                        select(evidence.c.object_key).where(
                            evidence.c.tenant_id == actor.tenant_id,
                            evidence.c.run_id == run_id,
                            evidence.c.source_type == "MATERIAL_UNIT",
                        )
                    ).scalars()
                )
                cited = session.execute(
                    select(material_unit.c.id, material_unit.c.sha256).where(
                        material_unit.c.tenant_id == actor.tenant_id,
                        material_unit.c.object_key.in_(cited_keys),
                    )
                ).all()
                if cited:
                    scope_views: list[dict[str, Any]] = [
                        {
                            "id": None,
                            "material_id": None,
                            "unit_refs": [f"material-unit:{row.id}@{row.sha256}" for row in cited],
                            "reason": "audit actually cited material evidence",
                            "required": True,
                            "scope_sha256": hashlib.sha256(
                                json.dumps(sorted(str(row.id) for row in cited)).encode("utf-8")
                            ).hexdigest(),
                        }
                    ]
                else:
                    scope_views = [dict(item) for item in scope_rows]
            else:
                scope_views = [dict(item) for item in scope_rows]
            projected_unit_ids: list[UUID] = []
            for scope_view in scope_views:
                for unit_ref in scope_view["unit_refs"]:
                    with suppress(ValueError, IndexError):
                        projected_unit_ids.append(UUID(str(unit_ref).split(":", 1)[1].split("@", 1)[0]))
            catalog_query = (
                select(
                    material_unit.c.id,
                    material_unit.c.material_id,
                    material_unit.c.parent_unit_id,
                    material_unit.c.unit_type,
                    material_unit.c.locator,
                    material_unit.c.tags,
                    material_unit.c.confidence,
                    material_unit.c.sha256,
                    material_unit.c.summary,
                    material.c.display_name,
                    material_analysis.c.coverage,
                )
                .join(
                    material,
                    (material.c.tenant_id == material_unit.c.tenant_id)
                    & (material.c.id == material_unit.c.material_id),
                )
                .join(
                    material_analysis,
                    (material_analysis.c.tenant_id == material_unit.c.tenant_id)
                    & (material_analysis.c.id == material_unit.c.analysis_id),
                )
                .where(
                    material_unit.c.tenant_id == actor.tenant_id,
                    material_unit.c.analysis_id.in_(included_analysis_ids),
                )
                .order_by(material.c.display_name, material_unit.c.ordinal)
            )
            if not planning_catalog:
                catalog_query = catalog_query.where(material_unit.c.id.in_(projected_unit_ids))
            raw_catalog_rows = session.execute(catalog_query.limit(1000)).mappings().all()
            if planning_catalog:
                catalog_rows = _representative_material_catalog_rows([dict(row) for row in raw_catalog_rows])
            else:
                catalog_rows = list(raw_catalog_rows)
        return _bounded_material_context(
            result,
            [dict(row) for row in catalog_rows],
            scope_views,
            include_unscoped_catalog=planning_catalog,
        )

    def material_read(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        unit_refs: list[str],
        purpose: str,
    ) -> dict[str, object]:
        if not 1 <= len(unit_refs) <= 8 or len(set(unit_refs)) != len(unit_refs):
            raise ValueError("material.read.v1 accepts 1 to 8 unique unit refs")
        purpose = purpose.strip()
        if not purpose or len(purpose) > 500:
            raise ValueError("material.read.v1 purpose must contain 1 to 500 characters")
        parameters = {"unit_refs": unit_refs, "purpose": purpose}
        parameters_sha = hashlib.sha256(
            json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        receipt_id = uuid4()
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assignment = self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            assert_run_active(session, actor.tenant_id, run_id)
            task_status = session.execute(
                select(task.c.status).where(
                    task.c.tenant_id == actor.tenant_id,
                    task.c.run_id == run_id,
                    task.c.id == task_id,
                )
            ).scalar_one()
            if task_status != "RUNNING":
                self._record_material_read_failure(
                    actor,
                    run_id,
                    task_id,
                    assignment,
                    receipt_id,
                    purpose,
                    unit_refs,
                    parameters_sha,
                    "SCOPE_DENIED",
                )
                raise MaterialScopeDenied("MATERIAL_SCOPE_DENIED")
            authorized: set[str] = set()
            for value in session.execute(
                select(task_material_scope.c.unit_refs).where(
                    task_material_scope.c.tenant_id == actor.tenant_id,
                    task_material_scope.c.run_id == run_id,
                    task_material_scope.c.task_id == task_id,
                )
            ).scalars():
                authorized.update(str(item) for item in value)
            if assignment.startswith("evidence-auditor@"):
                cited_keys = list(
                    session.execute(
                        select(evidence.c.object_key).where(
                            evidence.c.tenant_id == actor.tenant_id,
                            evidence.c.run_id == run_id,
                            evidence.c.source_type == "MATERIAL_UNIT",
                        )
                    ).scalars()
                )
                for row in session.execute(
                    select(material_unit.c.id, material_unit.c.sha256).where(
                        material_unit.c.tenant_id == actor.tenant_id,
                        material_unit.c.object_key.in_(cited_keys),
                    )
                ):
                    authorized.add(f"material-unit:{row.id}@{row.sha256}")
            denied = [ref for ref in unit_refs if ref not in authorized]
            if denied:
                self._record_material_read_failure(
                    actor,
                    run_id,
                    task_id,
                    assignment,
                    receipt_id,
                    purpose,
                    unit_refs,
                    parameters_sha,
                    "SCOPE_DENIED",
                )
                raise MaterialScopeDenied("MATERIAL_SCOPE_DENIED")
            unit_ids = [UUID(ref.split(":", 1)[1].split("@", 1)[0]) for ref in unit_refs]
            rows = (
                session.execute(
                    select(material_unit, material.c.display_name.label("material_display_name"))
                    .join(
                        material,
                        (material.c.tenant_id == material_unit.c.tenant_id)
                        & (material.c.id == material_unit.c.material_id),
                    )
                    .where(material_unit.c.tenant_id == actor.tenant_id, material_unit.c.id.in_(unit_ids))
                )
                .mappings()
                .all()
            )
            by_id = {UUID(str(row["id"])): row for row in rows}
            ordered = [by_id[value] for value in unit_ids if value in by_id]
            if len(ordered) != len(unit_ids):
                self._record_material_read_failure(
                    actor,
                    run_id,
                    task_id,
                    assignment,
                    receipt_id,
                    purpose,
                    unit_refs,
                    parameters_sha,
                    "SCOPE_DENIED",
                )
                raise MaterialScopeDenied("MATERIAL_SCOPE_DENIED")
            output_units: list[dict[str, object]] = []
            used = 0
            truncated = False
            for unit_row, unit_ref in zip(ordered, unit_refs, strict=True):
                body = self._objects.get_private(str(unit_row["object_key"]), max_bytes=2_000_000)
                if hashlib.sha256(body).hexdigest() != unit_row["sha256"]:
                    self._record_material_read_failure(
                        actor,
                        run_id,
                        task_id,
                        assignment,
                        receipt_id,
                        purpose,
                        unit_refs,
                        parameters_sha,
                        "INTEGRITY_FAILED",
                        mark_integrity=True,
                    )
                    raise MaterialIntegrityFailed("MATERIAL_INTEGRITY_FAILED")
                document = json.loads(body)
                evidence_id = uuid4()
                locator_id = uuid4()
                locator_draft = internal_material_source_locator(
                    display_name=str(unit_row["material_display_name"]),
                    fetched_at=now,
                    content_sha256=unit_row["sha256"],
                    locator=unit_row["locator"],
                )
                unit_output = {
                    "unit_ref": unit_ref,
                    "evidence_id": str(evidence_id),
                    "content": document.get("content", ""),
                    "visual_summary": document.get("visual_summary"),
                    "locator": unit_row["locator"],
                    "object_ref": unit_row["object_key"],
                    "sha256": unit_row["sha256"],
                    "source_locator": source_locator_view(locator_id, evidence_id, locator_draft),
                    "truncated": False,
                }
                encoded = json.dumps(unit_output, ensure_ascii=False, default=str).encode("utf-8")
                remaining = 62_000 - used
                if len(encoded) > remaining:
                    content = str(unit_output["content"]).encode("utf-8")[: max(0, remaining - 1000)]
                    unit_output["content"] = content.decode("utf-8", errors="ignore")
                    unit_output["truncated"] = True
                    truncated = True
                used += len(json.dumps(unit_output, ensure_ascii=False, default=str).encode("utf-8"))
                session.execute(
                    evidence.insert().values(
                        id=evidence_id,
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        task_id=task_id,
                        material_id=unit_row["material_id"],
                        source_type="MATERIAL_UNIT",
                        object_key=unit_row["object_key"],
                        sha256=unit_row["sha256"],
                        size_bytes=len(body),
                        mime_type="application/json",
                        evidence_level="E1",
                        trust_level="T1",
                        summary=unit_row["summary"][:4000],
                        published_at=None,
                        fetched_at=now,
                        valid_from=None,
                        valid_until=None,
                        region=None,
                        simulated=False,
                        supersedes_id=None,
                        created_at=now,
                    )
                )
                SourceLocatorRepository().append(
                    session,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    evidence_id=evidence_id,
                    locators=(locator_draft,),
                    locator_ids=(locator_id,),
                )
                output_units.append(unit_output)
                if truncated:
                    break
            result = {"receipt_id": str(receipt_id), "units": output_units, "truncated": truncated}
            result_sha = hashlib.sha256(
                json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            session.execute(
                material_read_receipt.insert().values(
                    id=receipt_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    agent_code=assignment.split("@", 1)[0],
                    purpose=purpose,
                    unit_refs=unit_refs,
                    parameters_sha256=parameters_sha,
                    result_sha256=result_sha,
                    status="SUCCEEDED",
                    created_at=now,
                )
            )
            return result

    def _record_material_read_failure(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        assignment: str,
        receipt_id: UUID,
        purpose: str,
        unit_refs: list[str],
        parameters_sha: str,
        status: str,
        *,
        mark_integrity: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            session.execute(
                material_read_receipt.insert().values(
                    id=receipt_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    agent_code=assignment.split("@", 1)[0],
                    purpose=purpose,
                    unit_refs=unit_refs,
                    parameters_sha256=parameters_sha,
                    result_sha256=None,
                    status=status,
                    created_at=now,
                )
            )
            if mark_integrity:
                session.execute(
                    update(task)
                    .where(task.c.tenant_id == actor.tenant_id, task.c.id == task_id)
                    .values(
                        status="NEEDS_ATTENTION",
                        last_failure_class="MATERIAL_INTEGRITY_FAILED",
                        last_error="material unit digest mismatch",
                        updated_at=now,
                    )
                )
                session.execute(
                    update(evaluation_run)
                    .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                    .values(
                        status="NEEDS_ATTENTION",
                        last_failure_class="MATERIAL_INTEGRITY_FAILED",
                        attention_reason="material unit digest mismatch",
                        updated_at=now,
                    )
                )

    def browser_audit(self, actor: Actor, run_id: UUID, task_id: UUID, url: str) -> dict[str, object]:
        self._assert_quota(actor, run_id, task_id, "BROWSER", maximum=BROWSER_CALLS_PER_TASK)
        invocation_id = self._start_tool(actor, run_id, task_id, "browser-audit.v1", {"url": url})
        try:
            artifact = self._browser.capture(url, timeout_seconds=120)
        except BrowserCaptureFailed:
            self._settle_tool(actor, run_id, invocation_id, "FAILED")
            raise
        except PublicResearchPolicyError:
            self._settle_tool(actor, run_id, invocation_id, "FAILED")
            raise
        except Exception as exc:
            self._settle_tool(
                actor, run_id, invocation_id, "SUBMISSION_UNKNOWN", "browser execution result state is unknown"
            )
            self._mark_unknown(actor, run_id, "browser execution result state is unknown")
            raise ExternalStateUnknown("browser execution result state is unknown") from exc
        metadata = {
            "final_url": artifact.final_url,
            "title": artifact.title,
            "fetched_at": artifact.fetched_at.isoformat(),
            "region": artifact.region,
            "dom_summary": artifact.dom_summary,
            "screenshot_sha256": hashlib.sha256(artifact.screenshot).hexdigest(),
        }
        try:
            screenshot_sha256 = hashlib.sha256(artifact.screenshot).hexdigest()
            evidence_id, object_key, digest, source_locators = self._persist(
                actor,
                run_id,
                task_id,
                "BROWSER",
                artifact.screenshot,
                "image/png",
                json.dumps(metadata, ensure_ascii=False),
                artifact.fetched_at,
                artifact.region,
                tool_invocation_id=invocation_id,
                source_locators=(
                    browser_source_locator(
                        final_url=artifact.final_url,
                        title=artifact.title,
                        fetched_at=artifact.fetched_at,
                        region=artifact.region,
                        screenshot_sha256=screenshot_sha256,
                    ),
                ),
            )
        except Exception as exc:
            self._settle_tool(
                actor, run_id, invocation_id, "SUBMISSION_UNKNOWN", "browser artifact persistence state is unknown"
            )
            self._mark_unknown(actor, run_id, "browser artifact persistence state is unknown")
            raise ExternalStateUnknown("browser artifact persistence state is unknown") from exc
        return {
            "evidence_id": str(evidence_id),
            "content_ref": {"ref": object_key, "sha256": digest},
            "source_locators": source_locators,
            **metadata,
        }

    def public_research_search(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        *,
        query: str,
        region: str,
        max_results: int = 5,
        days: int | None = None,
    ) -> dict[str, object]:
        query = query.strip()
        region = region.strip()
        if not query or len(query) > 400 or not region or len(region) > 100:
            raise ValueError("search query/region exceeds the bounded Tool Contract")
        if days is not None and not 1 <= days <= 365:
            raise ValueError("search days must be between 1 and 365")
        self._assert_quota(actor, run_id, task_id, "PUBLIC_RESEARCH", maximum=SEARCH_QUERIES_PER_TASK)
        invocation_id = self._start_tool(
            actor,
            run_id,
            task_id,
            "public-research-search.v1",
            {"query": query, "region": region, "max_results": min(max(1, max_results), 10), "days": days},
        )
        if self._search is None:
            self._search = TavilySearchAdapter()
        try:
            artifact = self._search.search(query, region=region, max_results=min(max(1, max_results), 10), days=days)
        except ExternalStateUnknown:
            self._settle_tool(
                actor, run_id, invocation_id, "SUBMISSION_UNKNOWN", "search submission or billing state is unknown"
            )
            self._mark_unknown(actor, run_id, "search submission or billing state is unknown")
            raise
        except (PublicResearchPolicyError, ValueError):
            self._settle_tool(actor, run_id, invocation_id, "FAILED")
            raise
        except Exception as exc:
            self._settle_tool(
                actor, run_id, invocation_id, "SUBMISSION_UNKNOWN", "search execution result state is unknown"
            )
            self._mark_unknown(actor, run_id, "search execution result state is unknown")
            raise ExternalStateUnknown("search execution result state is unknown") from exc
        if not artifact.usage_known or not artifact.submission_known:
            self._settle_tool(
                actor, run_id, invocation_id, "SUBMISSION_UNKNOWN", "search usage or submission state is unknown"
            )
            self._mark_unknown(actor, run_id, "search usage or submission state is unknown")
            raise ExternalStateUnknown("search usage or submission state is unknown")
        payload = json.dumps(
            {
                "query": artifact.query,
                "region": artifact.region,
                "fetched_at": artifact.fetched_at.isoformat(),
                "results": artifact.results,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            evidence_id, object_key, digest, source_locators = self._persist(
                actor,
                run_id,
                task_id,
                "PUBLIC_RESEARCH",
                payload,
                "application/json",
                f"Search results for {artifact.query}",
                artifact.fetched_at,
                artifact.region,
                tool_invocation_id=invocation_id,
                source_locators=search_source_locators(
                    artifact.results,
                    fetched_at=artifact.fetched_at,
                    region=artifact.region,
                ),
            )
        except Exception as exc:
            self._settle_tool(
                actor, run_id, invocation_id, "SUBMISSION_UNKNOWN", "search artifact persistence state is unknown"
            )
            self._mark_unknown(actor, run_id, "search artifact persistence state is unknown")
            raise ExternalStateUnknown("search artifact persistence state is unknown") from exc
        return {
            "evidence_id": str(evidence_id),
            "query": artifact.query,
            "content_ref": {"ref": object_key, "sha256": digest},
            "source_locators": source_locators,
            "region": artifact.region,
            "fetched_at": artifact.fetched_at.isoformat(),
            "results": list(artifact.results),
        }

    def _persist(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        source_type: str,
        payload: bytes,
        mime_type: str,
        summary: str,
        fetched_at: datetime,
        region: str,
        *,
        tool_invocation_id: UUID,
        source_locators: tuple[SourceLocatorDraft, ...] = (),
    ) -> tuple[UUID, str, str, list[dict[str, object]]]:
        evidence_id = uuid4()
        extension = "png" if mime_type == "image/png" else "json"
        key = f"tenant/{actor.tenant_id}/run/{run_id}/task/{task_id}/evidence/{evidence_id}.{extension}"
        digest = self._objects.put_private(key, payload, mime_type)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            assigned = (
                session.execute(
                    select(
                        task.c.skill_version_id,
                        task.c.skill_ref,
                        task.c.skill_version,
                        task.c.dispatch_epoch,
                    ).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.id == task_id,
                    )
                )
                .mappings()
                .one()
            )
            version_id = assigned["skill_version_id"]
            if version_id is None:
                version_id = session.execute(
                    select(skill_version.c.id).where(
                        skill_version.c.skill_code == assigned["skill_ref"],
                        skill_version.c.version == assigned["skill_version"],
                    )
                ).scalar_one()
            invocation_key = f"agentteams:{task_id}:{assigned['dispatch_epoch']}:skill"
            invocation_id = session.execute(
                select(skill_invocation.c.id).where(
                    skill_invocation.c.tenant_id == actor.tenant_id,
                    skill_invocation.c.idempotency_key == invocation_key,
                )
            ).scalar_one_or_none()
            if invocation_id is None:
                invocation_id = uuid4()
                session.execute(
                    skill_invocation.insert().values(
                        id=invocation_id,
                        tenant_id=actor.tenant_id,
                        task_id=task_id,
                        skill_version_id=version_id,
                        status="RUNNING",
                        idempotency_key=invocation_key,
                        estimated_cost=0,
                        created_at=datetime.now(UTC),
                    )
                )
            session.execute(
                evidence.insert().values(
                    id=evidence_id,
                    tenant_id=actor.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    material_id=None,
                    source_type=source_type,
                    object_key=key,
                    sha256=digest,
                    size_bytes=len(payload),
                    mime_type=mime_type,
                    evidence_level="E2",
                    trust_level="E2",
                    summary=summary[:4000],
                    fetched_at=fetched_at,
                    valid_until=fetched_at + timedelta(days=90),
                    region=region[:100],
                    simulated=False,
                    created_at=datetime.now(UTC),
                )
            )
            locator_ids = SourceLocatorRepository().append(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                evidence_id=evidence_id,
                locators=source_locators,
            )
            ExecutionControlApplication.settle_tool_invocation(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                invocation_id=tool_invocation_id,
                status="SUCCEEDED",
            )
        locator_views = [
            source_locator_view(locator_id, evidence_id, draft)
            for locator_id, draft in zip(locator_ids, source_locators, strict=True)
        ]
        return evidence_id, key, digest, locator_views

    def _start_tool(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        tool_code: str,
        parameters: dict[str, object],
    ) -> UUID:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assert_run_active(session, actor.tenant_id, run_id)
            self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            assigned = (
                session.execute(
                    select(
                        task.c.skill_version_id, task.c.skill_ref, task.c.skill_version, task.c.dispatch_epoch
                    ).where(
                        task.c.tenant_id == actor.tenant_id,
                        task.c.run_id == run_id,
                        task.c.id == task_id,
                    )
                )
                .mappings()
                .one()
            )
            version_id = assigned["skill_version_id"]
            if version_id is None:
                version_id = session.execute(
                    select(skill_version.c.id).where(
                        skill_version.c.skill_code == assigned["skill_ref"],
                        skill_version.c.version == assigned["skill_version"],
                    )
                ).scalar_one()
            invocation_key = f"agentteams:{task_id}:{assigned['dispatch_epoch']}:skill"
            skill_invocation_id = session.execute(
                select(skill_invocation.c.id).where(
                    skill_invocation.c.tenant_id == actor.tenant_id,
                    skill_invocation.c.idempotency_key == invocation_key,
                )
            ).scalar_one_or_none()
            if skill_invocation_id is None:
                skill_invocation_id = uuid4()
                session.execute(
                    skill_invocation.insert().values(
                        id=skill_invocation_id,
                        tenant_id=actor.tenant_id,
                        task_id=task_id,
                        skill_version_id=version_id,
                        status="RUNNING",
                        idempotency_key=invocation_key,
                        estimated_cost=0,
                        created_at=now,
                    )
                )
            parameters_sha256 = hashlib.sha256(
                json.dumps(parameters, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            invocation_id = uuid4()
            session.execute(
                tool_invocation.insert().values(
                    id=invocation_id,
                    tenant_id=actor.tenant_id,
                    skill_invocation_id=skill_invocation_id,
                    tool_code=tool_code,
                    risk_tier="LOW",
                    status="STARTED",
                    parameters_sha256=parameters_sha256,
                    created_at=now,
                )
            )
            return invocation_id

    def _settle_tool(
        self,
        actor: Actor,
        run_id: UUID,
        invocation_id: UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            ExecutionControlApplication.settle_tool_invocation(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                invocation_id=invocation_id,
                status=status,
                error=error,
            )

    @staticmethod
    def _require_assignment(
        session: Session, tenant_id: UUID, run_id: UUID, task_id: UUID, actor_id: str | None = None
    ) -> str:
        found = session.execute(
            select(task.c.agent_identity_ref).where(
                task.c.tenant_id == tenant_id, task.c.run_id == run_id, task.c.id == task_id
            )
        ).scalar_one_or_none()
        if found is None:
            raise NotFoundError("Run/Task assignment was not found")
        if actor_id and actor_id.startswith("agent:"):
            assigned_agent = found.split("@", 1)[0]
            if assigned_agent != actor_id.removeprefix("agent:"):
                raise NotFoundError("Run/Task assignment was not found")
        return found

    def _mark_unknown(self, actor: Actor, run_id: UUID, reason: str) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            result: Any = session.execute(
                update(evaluation_run)
                .where(evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id)
                .values(status="NEEDS_ATTENTION", last_failure_class="SUBMISSION_UNKNOWN", attention_reason=reason)
            )
            if result.rowcount != 1:
                raise NotFoundError("run was not found")

    def _assert_quota(self, actor: Actor, run_id: UUID, task_id: UUID, source_type: str, *, maximum: int) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            used = session.execute(
                select(evidence.c.id).where(
                    evidence.c.tenant_id == actor.tenant_id,
                    evidence.c.run_id == run_id,
                    evidence.c.task_id == task_id,
                    evidence.c.source_type == source_type,
                )
            ).all()
            if len(used) >= maximum:
                raise ValueError(f"{source_type} Tool quota exhausted for this Task")


def configured_browser_domains() -> tuple[str, ...]:
    return tuple(
        value.strip().lower()
        for value in os.getenv("LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS", "").split(",")
        if value.strip()
    )


def configured_authorized_case_urls() -> tuple[str, ...]:
    raw = os.getenv("LAUNCHSCOPE_AUTHORIZED_CASE_URL", "").strip()
    if not raw:
        return ()
    return (validate_public_https_url(raw, configured_browser_domains(), resolver=lambda _host: ["1.1.1.1"]),)


__all__ = [
    "BrowserArtifact",
    "BrowserCaptureFailed",
    "ExternalStateUnknown",
    "McpEvidenceApplication",
    "PlaywrightBrowserAdapter",
    "SearchArtifact",
    "TavilySearchAdapter",
    "configured_authorized_case_urls",
    "configured_browser_domains",
]
