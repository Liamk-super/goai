"""Authenticated read-only MCP capabilities with private durable Evidence output."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    evidence,
    finding,
    finding_evidence,
    product_profile,
    task,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.object_store import S3QuarantineObjectStore
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_domain.value_objects import TenantScope
from launchscope_worker.tools.public_research import PublicResearchPolicyError, validate_public_https_url


class ExternalStateUnknown(RuntimeError):
    """The caller cannot safely determine whether a paid/read-only side effect completed."""


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
    def search(
        self, query: str, *, region: str, max_results: int, days: int | None
    ) -> SearchArtifact: ...


class PlaywrightBrowserAdapter:
    def __init__(self, allowed_domains: tuple[str, ...]) -> None:
        self.allowed_domains = allowed_domains

    def capture(self, url: str, *, timeout_seconds: int) -> BrowserArtifact:
        allowed = validate_public_https_url(url, self.allowed_domains)
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

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
                page.goto(allowed, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                page.wait_for_load_state("networkidle", timeout=min(timeout_seconds, 30) * 1000)
                if blocked:
                    raise PublicResearchPolicyError("page attempted a request outside the frozen domain allowlist")
                final_url = validate_public_https_url(page.url, self.allowed_domains)
                title = page.title()[:500]
                summary = page.locator("body").inner_text(timeout=5000)[:4000]
                screenshot = page.screenshot(full_page=True, type="png")
                return BrowserArtifact(final_url, title, datetime.now(UTC), summary, screenshot, "GLOBAL")
            finally:
                browser.close()


class TavilySearchAdapter:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is required for real public research search")

    def search(
        self, query: str, *, region: str, max_results: int, days: int | None
    ) -> SearchArtifact:
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
                "url": item.get("url"), "title": item.get("title"),
                "content": str(item.get("content", ""))[:2000], "score": item.get("score"),
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
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            assignment = self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            run = session.execute(
                select(evaluation_run.c.product_version_id, evaluation_run.c.standard_version).where(
                    evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id
                )
            ).mappings().one()
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
            refs = session.execute(
                select(evidence.c.id, evidence.c.source_type, evidence.c.sha256)
                .where(evidence.c.tenant_id == actor.tenant_id, evidence.c.run_id == run_id)
                .limit(50)
            ).mappings().all()
            minimal_profile = (
                {str(key): profile[key] for key in sorted(profile)[:50]} if isinstance(profile, dict) else {}
            )
            audit_findings: list[dict[str, object]] = []
            if assignment.startswith("evidence-auditor@"):
                for item in session.execute(select(
                    finding.c.id, finding.c.dimension_code, finding.c.grade,
                    finding.c.statement, finding.c.is_hypothesis,
                ).where(
                    finding.c.tenant_id == actor.tenant_id, finding.c.run_id == run_id,
                )).mappings():
                    linked = session.execute(select(finding_evidence.c.evidence_id).where(
                        finding_evidence.c.tenant_id == actor.tenant_id,
                        finding_evidence.c.finding_id == item["id"],
                    )).scalars().all()
                    audit_findings.append({
                        "finding_id": str(item["id"]), "dimension": item["dimension_code"],
                        "proposed_grade": item["grade"], "statement": item["statement"][:2000],
                        "hypothesis": item["is_hypothesis"],
                        "evidence_ids": [str(value) for value in linked],
                    })
            result = {
                "run_id": str(run_id), "task_id": str(task_id),
                "standard_version": run["standard_version"], "product_profile": minimal_profile,
                "evidence_refs": [
                    {"evidence_id": str(item["id"]), "source_type": item["source_type"], "sha256": item["sha256"]}
                    for item in refs
                ],
                "audit_findings": audit_findings,
            }
            if len(json.dumps(result, default=str).encode("utf-8")) > 65_536:
                raise ValueError("minimal context exceeds the frozen MCP response budget")
            return result

    def browser_audit(self, actor: Actor, run_id: UUID, task_id: UUID, url: str) -> dict[str, object]:
        self._assert_quota(actor, run_id, task_id, "BROWSER", maximum=2)
        try:
            artifact = self._browser.capture(url, timeout_seconds=120)
        except PublicResearchPolicyError:
            raise
        except Exception as exc:
            self._mark_unknown(actor, run_id, "browser execution result state is unknown")
            raise ExternalStateUnknown("browser execution result state is unknown") from exc
        metadata = {
            "final_url": artifact.final_url, "title": artifact.title,
            "fetched_at": artifact.fetched_at.isoformat(), "region": artifact.region,
            "dom_summary": artifact.dom_summary, "screenshot_sha256": hashlib.sha256(artifact.screenshot).hexdigest(),
        }
        try:
            evidence_id = self._persist(
                actor, run_id, task_id, "BROWSER", artifact.screenshot, "image/png",
                json.dumps(metadata, ensure_ascii=False), artifact.fetched_at, artifact.region,
            )
        except Exception as exc:
            self._mark_unknown(actor, run_id, "browser artifact persistence state is unknown")
            raise ExternalStateUnknown("browser artifact persistence state is unknown") from exc
        return {"evidence_id": str(evidence_id), **metadata}

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
        self._assert_quota(actor, run_id, task_id, "PUBLIC_RESEARCH", maximum=8)
        if self._search is None:
            self._search = TavilySearchAdapter()
        try:
            artifact = self._search.search(
                query, region=region, max_results=min(max(1, max_results), 10), days=days
            )
        except ExternalStateUnknown:
            self._mark_unknown(actor, run_id, "search submission or billing state is unknown")
            raise
        if not artifact.usage_known or not artifact.submission_known:
            self._mark_unknown(actor, run_id, "search usage or submission state is unknown")
            raise ExternalStateUnknown("search usage or submission state is unknown")
        payload = json.dumps(
            {
                "query": artifact.query, "region": artifact.region,
                "fetched_at": artifact.fetched_at.isoformat(), "results": artifact.results,
            },
            sort_keys=True, ensure_ascii=False,
        ).encode("utf-8")
        try:
            evidence_id = self._persist(
                actor, run_id, task_id, "PUBLIC_RESEARCH", payload, "application/json",
                f"Search results for {artifact.query}", artifact.fetched_at, artifact.region,
            )
        except Exception as exc:
            self._mark_unknown(actor, run_id, "search artifact persistence state is unknown")
            raise ExternalStateUnknown("search artifact persistence state is unknown") from exc
        return {
            "evidence_id": str(evidence_id), "query": artifact.query,
            "region": artifact.region, "fetched_at": artifact.fetched_at.isoformat(),
            "results": list(artifact.results),
        }

    def _persist(
        self, actor: Actor, run_id: UUID, task_id: UUID, source_type: str,
        payload: bytes, mime_type: str, summary: str, fetched_at: datetime, region: str,
    ) -> UUID:
        evidence_id = uuid4()
        extension = "png" if mime_type == "image/png" else "json"
        key = f"tenant/{actor.tenant_id}/run/{run_id}/task/{task_id}/evidence/{evidence_id}.{extension}"
        digest = self._objects.put_private(key, payload, mime_type)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            session.execute(
                evidence.insert().values(
                    id=evidence_id, tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id,
                    material_id=None, source_type=source_type, object_key=key, sha256=digest,
                    size_bytes=len(payload), mime_type=mime_type, evidence_level="E2", trust_level="E2",
                    summary=summary[:4000], fetched_at=fetched_at, valid_until=fetched_at + timedelta(days=90),
                    region=region[:100], simulated=False, created_at=datetime.now(UTC),
                )
            )
        return evidence_id

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

    def _assert_quota(
        self, actor: Actor, run_id: UUID, task_id: UUID, source_type: str, *, maximum: int
    ) -> None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            self._require_assignment(session, actor.tenant_id, run_id, task_id, actor.actor_id)
            used = session.execute(
                select(evidence.c.id).where(
                    evidence.c.tenant_id == actor.tenant_id, evidence.c.run_id == run_id,
                    evidence.c.task_id == task_id, evidence.c.source_type == source_type,
                )
            ).all()
            if len(used) >= maximum:
                raise ValueError(f"{source_type} Tool quota exhausted for this Task")


def configured_browser_domains() -> tuple[str, ...]:
    return tuple(
        value.strip().lower() for value in os.getenv("LAUNCHSCOPE_BROWSER_ALLOWED_DOMAINS", "").split(",")
        if value.strip()
    )


__all__ = [
    "BrowserArtifact", "ExternalStateUnknown", "McpEvidenceApplication", "PlaywrightBrowserAdapter",
    "SearchArtifact", "TavilySearchAdapter", "configured_browser_domains",
]
