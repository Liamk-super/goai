"""Append-only human-readable source locators bound to persisted Evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import evidence, evidence_source_locator

_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "referrer"})


class SourceLocatorRelationError(ValueError):
    """The locator does not belong to the caller's tenant and Run Evidence."""


@dataclass(frozen=True, slots=True)
class SourceLocatorDraft:
    source_kind: str
    canonical_url: str | None
    title: str
    publisher: str | None
    published_at: datetime | None
    fetched_at: datetime
    locator: Mapping[str, object]
    region: str | None
    independence_group: str
    content_sha256: str
    screenshot_sha256: str | None = None


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    hostname = (parts.hostname or "").casefold()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("source URL must be absolute HTTP(S)")
    port = parts.port
    netloc = hostname
    if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{hostname}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_KEYS
    )
    return urlunsplit((scheme, netloc, path if path != "/" else "", urlencode(query, doseq=True), ""))


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9.]+", "-", value.casefold()).strip("-.")
    return normalized or "unknown"


def _independence_group(*, publisher: str | None, title: str, canonical_url: str | None) -> str:
    publisher_key = publisher
    if not publisher_key and canonical_url:
        publisher_key = urlsplit(canonical_url).hostname
    return f"{_slug(publisher_key or 'internal')}:{_slug(title)}"[:500]


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def browser_source_locator(
    *,
    final_url: str,
    title: str,
    fetched_at: datetime,
    region: str,
    screenshot_sha256: str,
) -> SourceLocatorDraft:
    canonical_url = canonicalize_url(final_url)
    return SourceLocatorDraft(
        source_kind="PUBLIC_URL",
        canonical_url=canonical_url,
        title=title.strip() or urlsplit(canonical_url).hostname or "Public source",
        publisher=None,
        published_at=None,
        fetched_at=fetched_at,
        locator={"kind": "browser_snapshot"},
        region=region.strip() or None,
        independence_group=_independence_group(publisher=None, title=title, canonical_url=canonical_url),
        content_sha256=screenshot_sha256,
        screenshot_sha256=screenshot_sha256,
    )


def search_source_locators(
    results: Iterable[Mapping[str, object]],
    *,
    fetched_at: datetime,
    region: str,
) -> tuple[SourceLocatorDraft, ...]:
    locators: list[SourceLocatorDraft] = []
    for result in results:
        raw_url = result.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        canonical_url = canonicalize_url(raw_url)
        title = str(result.get("title") or urlsplit(canonical_url).hostname or "Search result").strip()
        publisher_value = result.get("publisher")
        publisher = str(publisher_value).strip() if publisher_value else None
        payload = json.dumps(dict(result), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        locators.append(
            SourceLocatorDraft(
                source_kind="SEARCH_RESULT",
                canonical_url=canonical_url,
                title=title,
                publisher=publisher,
                published_at=_datetime(result.get("published_date")),
                fetched_at=fetched_at,
                locator={"kind": "search_result"},
                region=region.strip() or None,
                independence_group=_independence_group(
                    publisher=publisher,
                    title=title,
                    canonical_url=canonical_url,
                ),
                content_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(locators)


def internal_material_source_locator(
    *,
    display_name: str,
    fetched_at: datetime,
    content_sha256: str,
    locator: Mapping[str, object] | None = None,
) -> SourceLocatorDraft:
    title = display_name.strip() or "Internal Evidence"
    return SourceLocatorDraft(
        source_kind="INTERNAL_MATERIAL",
        canonical_url=None,
        title=title,
        publisher=None,
        published_at=None,
        fetched_at=fetched_at,
        locator=dict(locator or {}),
        region=None,
        independence_group=_independence_group(publisher="internal", title=title, canonical_url=None),
        content_sha256=content_sha256,
    )


def source_locator_view(
    locator_id: UUID,
    evidence_id: UUID,
    draft: SourceLocatorDraft,
) -> dict[str, object]:
    return {
        "source_locator_id": str(locator_id),
        "evidence_id": str(evidence_id),
        "source_kind": draft.source_kind,
        "canonical_url": draft.canonical_url,
        "title": draft.title,
        "publisher": draft.publisher,
        "published_at": draft.published_at.isoformat() if draft.published_at else None,
        "fetched_at": draft.fetched_at.isoformat(),
        "locator": dict(draft.locator),
        "region": draft.region,
        "independence_group": draft.independence_group,
        "content_sha256": draft.content_sha256,
    }


class SourceLocatorRepository:
    def append(
        self,
        session: Session,
        *,
        tenant_id: UUID,
        run_id: UUID,
        evidence_id: UUID,
        locators: Iterable[SourceLocatorDraft],
        locator_ids: Iterable[UUID] | None = None,
    ) -> tuple[UUID, ...]:
        drafts = tuple(locators)
        supplied_ids = tuple(locator_ids) if locator_ids is not None else None
        if supplied_ids is not None and len(supplied_ids) != len(drafts):
            raise ValueError("locator_ids must match the locator draft count")
        found = session.execute(
            select(evidence.c.id).where(
                evidence.c.tenant_id == tenant_id,
                evidence.c.run_id == run_id,
                evidence.c.id == evidence_id,
            )
        ).scalar_one_or_none()
        if found is None:
            raise SourceLocatorRelationError("Evidence does not belong to the caller's tenant and Run")
        ordinal = int(
            session.execute(
                select(func.coalesce(func.max(evidence_source_locator.c.ordinal), 0)).where(
                    evidence_source_locator.c.tenant_id == tenant_id,
                    evidence_source_locator.c.evidence_id == evidence_id,
                )
            ).scalar_one()
        )
        ids: list[UUID] = []
        for index, draft in enumerate(drafts):
            ordinal += 1
            locator_id = supplied_ids[index] if supplied_ids is not None else uuid4()
            session.execute(
                evidence_source_locator.insert().values(
                    id=locator_id,
                    tenant_id=tenant_id,
                    evidence_id=evidence_id,
                    ordinal=ordinal,
                    source_kind=draft.source_kind,
                    canonical_url=draft.canonical_url,
                    title=draft.title[:1000],
                    publisher=draft.publisher[:500] if draft.publisher else None,
                    published_at=draft.published_at,
                    fetched_at=draft.fetched_at,
                    locator=dict(draft.locator),
                    region=draft.region[:100] if draft.region else None,
                    independence_group=draft.independence_group[:500],
                    content_sha256=draft.content_sha256,
                    screenshot_sha256=draft.screenshot_sha256,
                    created_at=datetime.now(UTC),
                )
            )
            ids.append(locator_id)
        return tuple(ids)


__all__ = [
    "SourceLocatorDraft",
    "SourceLocatorRelationError",
    "SourceLocatorRepository",
    "browser_source_locator",
    "canonicalize_url",
    "internal_material_source_locator",
    "search_source_locators",
    "source_locator_view",
]
