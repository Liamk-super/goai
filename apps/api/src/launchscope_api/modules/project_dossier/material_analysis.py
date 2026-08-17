from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, TypedDict
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    material,
    material_analysis,
    material_selection,
    material_selection_item,
    material_unit,
    product_version,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.infrastructure.messaging.outbox import OutboxRepository
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.project_dossier.model_extraction import IntakeModelExtractor
from launchscope_domain.events import EventEnvelope
from launchscope_domain.value_objects import TenantScope
from launchscope_worker.material_parser import MaterialParseError, ParsedUnit, parse_material, render_pdf_page_jpeg

PARSER_VERSION = "material-parser-v1"
TERMINAL_STATUSES = frozenset({"READY", "PARTIAL", "FAILED", "NEEDS_CONSENT", "EXCLUDED"})
logger = logging.getLogger(__name__)


def _analysis_can_start(status: str) -> bool:
    return status == "QUEUED"


class MaterialAnalysisError(ValueError):
    pass


class MaterialScopeDenied(MaterialAnalysisError):
    pass


class MaterialIntegrityFailed(MaterialAnalysisError):
    pass


class MaterialObjectStore(Protocol):
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes: ...


class _VisualResult(TypedDict):
    summaries: dict[int, str]
    failed: list[dict[str, object]]
    model_id: str | None


class MaterialAnalysisApplication:
    def __init__(self, sessions: sessionmaker[Session], objects: MaterialObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects

    def enqueue(
        self,
        actor: Actor,
        material_id: UUID,
        *,
        allow_external_processing: bool,
        correlation_id: UUID,
        idempotency_key: str,
        force_retry: bool = False,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            source: Any = (
                session.execute(
                    select(material).where(material.c.tenant_id == actor.tenant_id, material.c.id == material_id)
                )
                .mappings()
                .first()
            )
            if source is None:
                raise NotFoundError("material was not found")
            if source["ingest_status"] != "VALIDATED":
                raise MaterialAnalysisError("only validated material may be analyzed")
            latest = (
                session.execute(
                    select(material_analysis)
                    .where(
                        material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.material_id == material_id
                    )
                    .order_by(material_analysis.c.attempt.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if latest is not None and (latest["status"] in {"QUEUED", "PARSING"} or not force_retry):
                return self._analysis_view(latest)
            if latest is not None and any(
                str(item.get("reason", "")).endswith("_UNKNOWN")
                for item in latest["coverage"].get("uncovered_locators", [])
                if isinstance(item, dict)
            ):
                raise MaterialAnalysisError("unknown visual submission or billing state cannot be retried")
            attempt = 0 if latest is None else int(latest["attempt"]) + 1
            if attempt > 1:
                raise MaterialAnalysisError("material analysis retry limit is exhausted")
            analysis_id = uuid4()
            coverage = {"total": 0, "parsed": 0, "visual_inspected": 0, "uncovered_locators": []}
            session.execute(
                material_analysis.insert().values(
                    id=analysis_id,
                    tenant_id=actor.tenant_id,
                    material_id=material_id,
                    product_version_id=source["product_version_id"],
                    status="QUEUED",
                    attempt=attempt,
                    parser_version=PARSER_VERSION,
                    model_id=None,
                    manifest_object_key=None,
                    manifest_sha256=None,
                    page_count=0,
                    unit_count=0,
                    coverage=coverage,
                    error_code=None,
                    error_message=None,
                    external_consent=allow_external_processing,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
            )
            event = EventEnvelope(
                event_type="material.analysis.requested.v1",
                tenant_id=actor.tenant_id,
                run_id=material_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                payload={
                    "analysis_id": str(analysis_id),
                    "material_id": str(material_id),
                    "product_version_id": str(source["product_version_id"]),
                    "attempt": attempt,
                    "allow_external_processing": allow_external_processing,
                },
            )
            OutboxRepository(session).enqueue(
                event,
                aggregate_id=material_id,
                aggregate_type="material",
                scope=TenantScope(actor.tenant_id),
            )
            return {
                "analysis_id": str(analysis_id),
                "material_id": str(material_id),
                "status": "QUEUED",
                "attempt": attempt,
                "coverage": coverage,
            }

    def process(self, actor: Actor, analysis_id: UUID) -> dict[str, object]:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            row: Any = (
                session.execute(
                    select(material_analysis)
                    .where(material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.id == analysis_id)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                raise NotFoundError("material analysis was not found")
            if row["status"] in TERMINAL_STATUSES:
                return self._analysis_view(row)
            if not _analysis_can_start(str(row["status"])):
                if row["status"] == "PARSING":
                    return self._analysis_view(row)
                raise MaterialAnalysisError("material analysis is not processable")
            session.execute(
                update(material_analysis)
                .where(material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.id == analysis_id)
                .values(status="PARSING", updated_at=now)
            )
            source: Any = (
                session.execute(
                    select(material).where(
                        material.c.tenant_id == actor.tenant_id,
                        material.c.id == row["material_id"],
                        material.c.ingest_status == "VALIDATED",
                    )
                )
                .mappings()
                .one()
            )
        try:
            payload = self._objects.get_private(str(source["object_key"]), max_bytes=20 * 1024 * 1024)
            if hashlib.sha256(payload).hexdigest() != source["sha256"]:
                raise MaterialIntegrityFailed("MATERIAL_INTEGRITY_FAILED: source sha256 mismatch")
            parsed = parse_material(payload, str(source["mime_type"]), str(source["display_name"]))
            visual = self._visual_analysis(
                payload,
                str(source["mime_type"]),
                str(source["display_name"]),
                parsed.visual_candidates,
                allow_external_processing=bool(row["external_consent"]),
            )
            unit_documents = self._store_units(actor, analysis_id, source, parsed.units, visual["summaries"])
            uncovered = [
                locator
                for locator in parsed.uncovered_locators
                if _visual_locator_index(locator) not in visual["summaries"]
            ]
            uncovered.extend(visual["failed"])
            status = "PARTIAL" if uncovered else "READY"
            if parsed.visual_candidates and not row["external_consent"]:
                status = "NEEDS_CONSENT"
            coverage = {
                "total": parsed.page_count if str(source["mime_type"]) == "application/pdf" else len(parsed.units),
                "parsed": parsed.parsed_count,
                "visual_inspected": len(visual["summaries"]),
                "uncovered_locators": uncovered,
            }
            return self._complete(
                actor,
                analysis_id,
                source,
                status=status,
                page_count=parsed.page_count,
                coverage=coverage,
                unit_documents=unit_documents,
                model_id=visual["model_id"],
            )
        except MaterialParseError as exc:
            return self._fail(actor, analysis_id, str(exc).split(":", 1)[0], str(exc))
        except MaterialIntegrityFailed:
            return self._fail(actor, analysis_id, "MATERIAL_INTEGRITY_FAILED", "source material integrity check failed")
        except Exception as exc:
            return self._fail(actor, analysis_id, "MATERIAL_ANALYSIS_FAILED", str(exc)[:2000])

    def process_queued(self, actor: Actor, *, limit: int = 10) -> list[dict[str, object]]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            ids = list(
                session.execute(
                    select(material_analysis.c.id)
                    .where(material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.status == "QUEUED")
                    .order_by(material_analysis.c.created_at)
                    .limit(max(1, min(limit, 50)))
                ).scalars()
            )
        return [self.process(actor, analysis_id) for analysis_id in ids]

    def list_for_version(self, actor: Actor, product_version_id: UUID) -> list[dict[str, object]]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            rows = (
                session.execute(
                    select(material_analysis, material.c.display_name, material.c.mime_type, material.c.sha256)
                    .join(
                        material,
                        (material.c.tenant_id == material_analysis.c.tenant_id)
                        & (material.c.id == material_analysis.c.material_id),
                    )
                    .where(
                        material_analysis.c.tenant_id == actor.tenant_id,
                        material_analysis.c.product_version_id == product_version_id,
                    )
                    .order_by(material_analysis.c.material_id, material_analysis.c.attempt.desc())
                )
                .mappings()
                .all()
            )
        latest: dict[UUID, dict[str, object]] = {}
        for row in rows:
            material_id = UUID(str(row["material_id"]))
            if material_id in latest:
                continue
            view = self._analysis_view(row)
            view.update(
                {
                    "display_name": row["display_name"],
                    "mime_type": row["mime_type"],
                    "source_sha256": row["sha256"],
                }
            )
            latest[material_id] = view
        return list(latest.values())

    def submit_selection(
        self,
        actor: Actor,
        product_version_id: UUID,
        items: list[dict[str, object]],
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        if not items:
            raise MaterialAnalysisError("material selection requires at least one item")
        now = datetime.now(UTC)
        request_sha = hashlib.sha256(
            json.dumps(
                {"product_version_id": str(product_version_id), "items": items},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            replay = (
                session.execute(
                    select(material_selection).where(
                        material_selection.c.tenant_id == actor.tenant_id,
                        material_selection.c.idempotency_key == idempotency_key,
                    )
                )
                .mappings()
                .first()
            )
            if replay is not None:
                if replay["request_sha256"] != request_sha:
                    raise MaterialAnalysisError("IDEMPOTENCY_CONFLICT")
                return self._selection_view(session, replay)
            version_exists = session.execute(
                select(product_version.c.id).where(
                    product_version.c.tenant_id == actor.tenant_id,
                    product_version.c.id == product_version_id,
                )
            ).scalar_one_or_none()
            if version_exists is None:
                raise NotFoundError("product version was not found")
            normalized: list[dict[str, object]] = []
            for item in items:
                material_id = UUID(str(item.get("material_id")))
                analysis_id = UUID(str(item.get("analysis_id")))
                decision = str(item.get("decision") or "")
                acknowledged = item.get("acknowledged_uncovered_locators") or []
                analysis = (
                    session.execute(
                        select(material_analysis).where(
                            material_analysis.c.tenant_id == actor.tenant_id,
                            material_analysis.c.id == analysis_id,
                            material_analysis.c.material_id == material_id,
                            material_analysis.c.product_version_id == product_version_id,
                        )
                    )
                    .mappings()
                    .first()
                )
                if analysis is None:
                    raise MaterialAnalysisError("material selection references an unavailable analysis")
                status = str(analysis["status"])
                if status not in TERMINAL_STATUSES:
                    raise MaterialAnalysisError("all material analyses must be terminal before selection")
                if decision == "INCLUDE" and status != "READY":
                    raise MaterialAnalysisError("only READY material may be included without a partial acknowledgement")
                if decision == "INCLUDE_PARTIAL" and status != "PARTIAL":
                    raise MaterialAnalysisError("INCLUDE_PARTIAL requires a PARTIAL analysis")
                if status == "PARTIAL" and decision == "INCLUDE_PARTIAL" and not acknowledged:
                    raise MaterialAnalysisError("partial material requires acknowledged uncovered locators")
                if status in {"FAILED", "NEEDS_CONSENT"} and decision != "EXCLUDE":
                    raise MaterialAnalysisError("failed or consent-blocked material must be excluded")
                if decision not in {"INCLUDE", "INCLUDE_PARTIAL", "EXCLUDE"}:
                    raise MaterialAnalysisError("material selection decision is invalid")
                normalized.append(
                    {
                        "material_id": str(material_id),
                        "analysis_id": str(analysis_id),
                        "decision": decision,
                        "acknowledged_uncovered_locators": acknowledged,
                    }
                )
            revision = (
                int(
                    session.execute(
                        select(func.coalesce(func.max(material_selection.c.revision), 0)).where(
                            material_selection.c.tenant_id == actor.tenant_id,
                            material_selection.c.product_version_id == product_version_id,
                        )
                    ).scalar_one()
                )
                + 1
            )
            selection_id = uuid4()
            document = {
                "schema_version": "1.0",
                "selection_id": str(selection_id),
                "product_version_id": str(product_version_id),
                "revision": revision,
                "items": normalized,
                "confirmed_by": actor.actor_id,
                "confirmed_at": now.isoformat().replace("+00:00", "Z"),
            }
            body = _canonical(document)
            key = (
                f"tenant/{actor.tenant_id}/product-version/{product_version_id}/material-selection/{selection_id}.json"
            )
            digest = self._objects.put_private(key, body, "application/json")
            session.execute(
                material_selection.insert().values(
                    id=selection_id,
                    tenant_id=actor.tenant_id,
                    product_version_id=product_version_id,
                    revision=revision,
                    idempotency_key=idempotency_key,
                    request_sha256=request_sha,
                    object_key=key,
                    sha256=digest,
                    confirmed_by=actor.actor_id,
                    confirmed_at=now,
                    created_at=now,
                )
            )
            for item in normalized:
                session.execute(
                    material_selection_item.insert().values(
                        id=uuid4(),
                        tenant_id=actor.tenant_id,
                        selection_id=selection_id,
                        material_id=UUID(str(item["material_id"])),
                        analysis_id=UUID(str(item["analysis_id"])),
                        decision=item["decision"],
                        acknowledged_uncovered_locators=item["acknowledged_uncovered_locators"],
                        created_at=now,
                    )
                )
            return {**document, "object_key": key, "sha256": digest}

    def latest_selection(self, actor: Actor, product_version_id: UUID) -> dict[str, object] | None:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            selection = (
                session.execute(
                    select(material_selection)
                    .where(
                        material_selection.c.tenant_id == actor.tenant_id,
                        material_selection.c.product_version_id == product_version_id,
                    )
                    .order_by(material_selection.c.revision.desc())
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if selection is None:
                return None
            return self._selection_view(session, selection)

    @staticmethod
    def _selection_view(session: Session, selection: Any) -> dict[str, object]:
        items = (
            session.execute(
                select(material_selection_item).where(
                    material_selection_item.c.tenant_id == selection["tenant_id"],
                    material_selection_item.c.selection_id == selection["id"],
                )
            )
            .mappings()
            .all()
        )
        return {
            "selection_id": str(selection["id"]),
            "product_version_id": str(selection["product_version_id"]),
            "revision": selection["revision"],
            "object_key": selection["object_key"],
            "sha256": selection["sha256"],
            "items": [
                {
                    "material_id": str(item["material_id"]),
                    "analysis_id": str(item["analysis_id"]),
                    "decision": item["decision"],
                    "acknowledged_uncovered_locators": item["acknowledged_uncovered_locators"],
                }
                for item in items
            ],
        }

    def included_context(self, actor: Actor, product_version_id: UUID, *, limit: int = 30_000) -> str:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            selection = session.execute(
                select(material_selection.c.id)
                .where(
                    material_selection.c.tenant_id == actor.tenant_id,
                    material_selection.c.product_version_id == product_version_id,
                )
                .order_by(material_selection.c.revision.desc())
                .limit(1)
            ).scalar_one_or_none()
            if selection is None:
                return ""
            analysis_ids = list(
                session.execute(
                    select(material_selection_item.c.analysis_id).where(
                        material_selection_item.c.tenant_id == actor.tenant_id,
                        material_selection_item.c.selection_id == selection,
                        material_selection_item.c.decision.in_(("INCLUDE", "INCLUDE_PARTIAL")),
                    )
                ).scalars()
            )
            rows = (
                session.execute(
                    select(
                        material_unit.c.id, material_unit.c.locator, material_unit.c.summary, material.c.display_name
                    )
                    .join(
                        material,
                        (material.c.tenant_id == material_unit.c.tenant_id)
                        & (material.c.id == material_unit.c.material_id),
                    )
                    .where(material_unit.c.tenant_id == actor.tenant_id, material_unit.c.analysis_id.in_(analysis_ids))
                    .order_by(material.c.display_name, material_unit.c.ordinal)
                )
                .mappings()
                .all()
            )
        chunks: list[str] = []
        used = 0
        for row in rows:
            chunk = f"【{row['display_name']} {json.dumps(row['locator'], ensure_ascii=False)}】\n{row['summary']}\n"
            if used + len(chunk) > limit:
                break
            chunks.append(chunk)
            used += len(chunk)
        return "\n".join(chunks)

    def _store_units(
        self,
        actor: Actor,
        analysis_id: UUID,
        source: Any,
        units: tuple[ParsedUnit, ...],
        visual_summaries: dict[int, str],
    ) -> list[dict[str, object]]:
        now = datetime.now(UTC)
        unit_ids = {int(unit.ordinal): uuid4() for unit in units}
        documents: list[dict[str, object]] = []
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            for unit in units:
                ordinal = int(unit.ordinal)
                locator = dict(unit.locator)
                visual_index = _visual_locator_index(locator)
                visual_summary = visual_summaries.get(visual_index) if visual_index is not None else None
                content = str(unit.content)
                if visual_summary:
                    content = f"{content}\n\n视觉识别: {visual_summary}".strip()
                body_document = {
                    "schema_version": "1.0",
                    "unit_id": str(unit_ids[ordinal]),
                    "analysis_id": str(analysis_id),
                    "material_id": str(source["id"]),
                    "locator": locator,
                    "content": content,
                    "visual_summary": visual_summary,
                }
                body = _canonical(body_document)
                key = (
                    f"tenant/{actor.tenant_id}/product-version/{source['product_version_id']}/material-analysis/"
                    f"{analysis_id}/units/{unit_ids[ordinal]}.json"
                )
                digest = self._objects.put_private(key, body, "application/json")
                summary = (visual_summary or str(unit.summary))[:2000]
                session.execute(
                    material_unit.insert().values(
                        id=unit_ids[ordinal],
                        tenant_id=actor.tenant_id,
                        analysis_id=analysis_id,
                        material_id=source["id"],
                        product_version_id=source["product_version_id"],
                        parent_unit_id=unit_ids.get(unit.parent_ordinal) if unit.parent_ordinal is not None else None,
                        ordinal=ordinal,
                        unit_type=unit.unit_type,
                        locator=locator,
                        tags=list(unit.tags),
                        confidence=Decimal(str(unit.confidence)),
                        contains_sensitive_data=unit.contains_sensitive_data,
                        object_key=key,
                        sha256=digest,
                        summary=summary,
                        created_at=now,
                    )
                )
                documents.append(
                    {
                        "unit_id": str(unit_ids[ordinal]),
                        "unit_ref": f"material-unit:{unit_ids[ordinal]}@{digest}",
                        "parent_unit_id": str(unit_ids[unit.parent_ordinal])
                        if unit.parent_ordinal in unit_ids
                        else None,
                        "unit_type": unit.unit_type,
                        "locator": locator,
                        "tags": list(unit.tags),
                        "confidence": float(unit.confidence),
                        "contains_sensitive_data": unit.contains_sensitive_data,
                        "content_ref": {"object_key": key, "sha256": digest},
                        "summary": summary,
                    }
                )
        return documents

    def _complete(
        self,
        actor: Actor,
        analysis_id: UUID,
        source: Any,
        *,
        status: str,
        page_count: int,
        coverage: dict[str, object],
        unit_documents: list[dict[str, object]],
        model_id: str | None,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        if str(source["mime_type"]) == "application/pdf":
            legacy = {
                "schema_version": "1.0",
                "source": {"object_key": source["object_key"], "sha256": source["sha256"]},
                "page_count": page_count,
                "coverage": coverage,
                "units": [
                    {
                        "unit_ref": item["unit_ref"],
                        "locator": item["locator"],
                        "summary": item["summary"],
                    }
                    for item in unit_documents
                    if item["unit_type"] in {"PAGE", "TABLE", "IMAGE"}
                ],
            }
            legacy_body = _canonical(legacy)
            legacy_key = (
                f"tenant/{actor.tenant_id}/product-version/{source['product_version_id']}/material-analysis/"
                f"{analysis_id}/legacy-pdf-analysis.json"
            )
            legacy_digest = self._objects.put_private(legacy_key, legacy_body, "application/json")
            coverage = {
                **coverage,
                "legacy_pdf_analysis_ref": {"object_key": legacy_key, "sha256": legacy_digest},
            }
        root_refs = [item["unit_ref"] for item in unit_documents if item["parent_unit_id"] is None]
        manifest = {
            "schema_version": "1.0",
            "manifest_id": str(analysis_id),
            "material_id": str(source["id"]),
            "product_version_id": str(source["product_version_id"]),
            "source_ref": {"object_key": source["object_key"], "sha256": source["sha256"]},
            "status": status,
            "parser_version": PARSER_VERSION,
            "model_id": model_id,
            "page_count": page_count,
            "unit_count": len(unit_documents),
            "coverage": coverage,
            "root_unit_refs": root_refs,
            "error_code": None,
            "error_message": None,
            "created_at": now.isoformat().replace("+00:00", "Z"),
        }
        body = _canonical(manifest)
        key = (
            f"tenant/{actor.tenant_id}/product-version/{source['product_version_id']}/material-analysis/"
            f"{analysis_id}/manifest.json"
        )
        digest = self._objects.put_private(key, body, "application/json")
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            session.execute(
                update(material_analysis)
                .where(material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.id == analysis_id)
                .values(
                    status=status,
                    model_id=model_id,
                    manifest_object_key=key,
                    manifest_sha256=digest,
                    page_count=page_count,
                    unit_count=len(unit_documents),
                    coverage=coverage,
                    error_code=None,
                    error_message=None,
                    updated_at=now,
                    completed_at=now,
                )
            )
        return {**manifest, "analysis_id": str(analysis_id), "manifest_object_key": key, "manifest_sha256": digest}

    def _fail(self, actor: Actor, analysis_id: UUID, code: str, message: str) -> dict[str, object]:
        now = datetime.now(UTC)
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            session.execute(
                update(material_analysis)
                .where(material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.id == analysis_id)
                .values(
                    status="FAILED", error_code=code, error_message=message[:2000], updated_at=now, completed_at=now
                )
            )
            row: Any = (
                session.execute(
                    select(material_analysis).where(
                        material_analysis.c.tenant_id == actor.tenant_id, material_analysis.c.id == analysis_id
                    )
                )
                .mappings()
                .one()
            )
            return self._analysis_view(row)

    def _visual_analysis(
        self,
        payload: bytes,
        mime_type: str,
        display_name: str,
        candidates: tuple[int, ...],
        *,
        allow_external_processing: bool,
    ) -> _VisualResult:
        if not candidates or not allow_external_processing:
            return {"summaries": {}, "failed": [], "model_id": None}
        extractor = IntakeModelExtractor()
        summaries: dict[int, str] = {}
        failed: list[dict[str, object]] = []
        model_id: str | None = None
        for page_number in candidates[:24]:
            try:
                jpeg = self._visual_jpeg(payload, mime_type, page_number)
                result = extractor.analyze_visual_page(
                    file_name=display_name,
                    page_number=page_number,
                    image_data_url="data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
                    text_hint="",
                    allow_external_processing=True,
                )
                summaries[page_number] = str(result["summary"])
                model_id = str(result["model_id"])
            except Exception as exc:
                logger.exception(
                    "authoritative visual analysis failed for %s page %s",
                    display_name,
                    page_number,
                )
                message = str(exc).lower()
                unknown = any(token in message for token in ("submission unknown", "billing unknown", "usage unknown"))
                failed.append({"page": page_number, "reason": "VISION_STATE_UNKNOWN" if unknown else "VISION_FAILED"})
                break
        return {"summaries": summaries, "failed": failed, "model_id": model_id}

    @staticmethod
    def _visual_jpeg(payload: bytes, mime_type: str, page_number: int) -> bytes:
        if mime_type == "application/pdf":
            return render_pdf_page_jpeg(payload, page_number)
        from PIL import Image

        source = payload
        if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                images = sorted(item for item in archive.namelist() if item.startswith("word/media/"))
                source = archive.read(images[page_number - 1])
        with Image.open(io.BytesIO(source)) as image:
            converted = image.convert("RGB")
            output = io.BytesIO()
            converted.save(output, "JPEG", quality=72, optimize=True)
            return output.getvalue()

    @staticmethod
    def _analysis_view(row: Any) -> dict[str, object]:
        return {
            "analysis_id": str(row["id"]),
            "material_id": str(row["material_id"]),
            "product_version_id": str(row["product_version_id"]),
            "status": row["status"],
            "attempt": row["attempt"],
            "parser_version": row["parser_version"],
            "model_id": row["model_id"],
            "manifest_object_key": row["manifest_object_key"],
            "manifest_sha256": row["manifest_sha256"],
            "page_count": row["page_count"],
            "unit_count": row["unit_count"],
            "coverage": row["coverage"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "external_consent": row["external_consent"],
        }


def material_routing_enabled() -> bool:
    return os.getenv("LAUNCHSCOPE_MATERIAL_ROUTING_V2_ENABLED", "false").lower() == "true"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _visual_locator_index(locator: Mapping[str, object]) -> int | None:
    for key in ("page", "embedded_image", "image"):
        value = locator.get(key)
        if isinstance(value, int):
            return value
    return None


__all__ = [
    "MaterialAnalysisApplication",
    "MaterialAnalysisError",
    "MaterialIntegrityFailed",
    "MaterialScopeDenied",
    "material_routing_enabled",
]
