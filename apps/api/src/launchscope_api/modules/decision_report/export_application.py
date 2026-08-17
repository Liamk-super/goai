from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    agent_report_artifact,
    evidence,
    report,
    report_export_artifact,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.identity_tenant.application import Actor, NotFoundError
from launchscope_api.modules.project_dossier.material_ingestion import ObjectMetadata
from launchscope_api.modules.user_validation.application import IdempotencyConflictError as BaseIdempotencyConflictError
from launchscope_domain.value_objects import TenantScope

from .export_renderer import (
    RENDERER_VERSION,
    EvidenceArchiveEntry,
    PrintTarget,
    assemble_report_package,
)

ExportKind = Literal["SUPERVISOR", "SPECIALIST", "PACKAGE"]
ExportView = Literal["SUMMARY", "FULL"]
_SPECIALISTS = (
    ("user-evidence", "用户报告.pdf"),
    ("product-engineering", "产品经理报告.pdf"),
    ("business-investment", "投资人报告.pdf"),
    ("evidence-auditor", "证据校准报告.pdf"),
)


class ExportObjectStore(Protocol):
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...
    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes: ...
    def head(self, object_key: str) -> ObjectMetadata | None: ...
    def signed_read_url(self, object_key: str) -> str: ...


class PdfRenderer(Protocol):
    def render_pdf(self, target: PrintTarget) -> bytes: ...


class IdempotencyConflictError(BaseIdempotencyConflictError):
    pass


class ReportExportIntegrityError(RuntimeError):
    pass


class ReportExportBusyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExportRequest:
    kind: ExportKind
    agent_code: str | None = None
    view: ExportView = "FULL"
    locale: str = "zh-CN"
    include_evidence: bool = False

    def __post_init__(self) -> None:
        if self.kind not in {"SUPERVISOR", "SPECIALIST", "PACKAGE"}:
            raise ValueError("unsupported report export kind")
        if self.view not in {"SUMMARY", "FULL"}:
            raise ValueError("unsupported report export view")
        if not self.locale or len(self.locale) > 20:
            raise ValueError("report export locale is invalid")
        if self.kind == "SPECIALIST" and self.agent_code not in {item[0] for item in _SPECIALISTS}:
            raise ValueError("specialist export requires one known agent code")
        if self.kind != "SPECIALIST" and self.agent_code is not None:
            raise ValueError("agent code is only valid for specialist exports")
        if self.kind != "PACKAGE" and self.include_evidence:
            raise ValueError("Evidence originals are only valid for complete report packages")


@dataclass(frozen=True, slots=True)
class ExportResult:
    export_id: UUID
    report_id: UUID
    run_id: UUID
    kind: str
    agent_code: str | None
    view: str
    locale: str
    include_evidence: bool
    source_sha256: str
    status: str
    object_key: str | None
    sha256: str | None
    size_bytes: int | None
    error_code: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "export_id": str(self.export_id),
            "report_id": str(self.report_id),
            "run_id": str(self.run_id),
        }


@dataclass(frozen=True, slots=True)
class _CanonicalReport:
    report_id: UUID
    run_id: UUID
    agent_code: str | None
    object_key: str
    sha256: str
    document: dict[str, object]


class ReportExportApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: ExportObjectStore,
        renderer: PdfRenderer,
        *,
        renderer_version: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._renderer = renderer
        self._renderer_version = renderer_version or getattr(renderer, "version", RENDERER_VERSION)

    def create(
        self,
        actor: Actor,
        report_id: UUID,
        request: ExportRequest,
        *,
        idempotency_key: str,
    ) -> ExportResult:
        if not idempotency_key or len(idempotency_key) > 255:
            raise ValueError("Idempotency-Key is required and must not exceed 255 characters")
        sources = self._load_sources(actor, report_id, request)
        run_id = sources[0].run_id
        source_sha256 = self._source_sha256(sources)
        request_sha256 = self._request_sha256(report_id, request)
        export_id = uuid4()

        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            idempotent = session.execute(
                select(report_export_artifact).where(
                    report_export_artifact.c.tenant_id == actor.tenant_id,
                    report_export_artifact.c.idempotency_key == idempotency_key,
                )
            ).mappings().first()
            if idempotent is not None:
                if str(idempotent["request_sha256"]) != request_sha256:
                    raise IdempotencyConflictError("Idempotency-Key was reused with a different export request")
                if str(idempotent["source_sha256"]) != source_sha256:
                    raise IdempotencyConflictError("Idempotency-Key was reused after the canonical report changed")
                if idempotent["status"] == "FAILED":
                    session.execute(
                        report_export_artifact.update()
                        .where(report_export_artifact.c.id == idempotent["id"])
                        .values(status="RENDERING", error_code=None)
                    )
                    export_id = UUID(str(idempotent["id"]))
                else:
                    return self._result(dict(idempotent))
            else:
                cached = session.execute(
                    select(report_export_artifact).where(
                        report_export_artifact.c.tenant_id == actor.tenant_id,
                        report_export_artifact.c.report_id == report_id,
                        report_export_artifact.c.agent_code.is_(None)
                        if request.agent_code is None
                        else report_export_artifact.c.agent_code == request.agent_code,
                        report_export_artifact.c.kind == request.kind,
                        report_export_artifact.c.view == request.view,
                        report_export_artifact.c.locale == request.locale,
                        report_export_artifact.c.include_evidence == request.include_evidence,
                        report_export_artifact.c.renderer_version == self._renderer_version,
                        report_export_artifact.c.source_sha256 == source_sha256,
                    )
                ).mappings().first()
                if cached is not None:
                    if cached["status"] == "COMPLETED":
                        return self._result(dict(cached))
                    if cached["status"] in {"PENDING", "RENDERING"}:
                        return self._result(dict(cached))
                    raise ReportExportBusyError(
                        "failed cached export must be retried with its original Idempotency-Key"
                    )
                now = datetime.now(UTC)
                session.execute(
                    report_export_artifact.insert().values(
                        id=export_id,
                        tenant_id=actor.tenant_id,
                        run_id=run_id,
                        report_id=report_id,
                        agent_code=request.agent_code,
                        kind=request.kind,
                        view=request.view,
                        locale=request.locale,
                        include_evidence=request.include_evidence,
                        renderer_version=self._renderer_version,
                        source_sha256=source_sha256,
                        idempotency_key=idempotency_key,
                        request_sha256=request_sha256,
                        status="RENDERING",
                        created_at=now,
                    )
                )

        try:
            payload, mime_type, suffix = self._render(actor, sources, request)
            if not payload:
                raise ReportExportIntegrityError("renderer returned an empty export")
            object_key = (
                f"{actor.tenant_id}/runs/{run_id}/report-exports/{export_id}/"
                f"{source_sha256[:16]}.{suffix}"
            )
            digest = self._objects.put_private(object_key, payload, mime_type)
            if digest != hashlib.sha256(payload).hexdigest():
                raise ReportExportIntegrityError("export object store returned a different digest")
        except Exception as exc:
            with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
                session.execute(
                    report_export_artifact.update()
                    .where(
                        report_export_artifact.c.tenant_id == actor.tenant_id,
                        report_export_artifact.c.id == export_id,
                    )
                    .values(status="FAILED", error_code=type(exc).__name__[:120], completed_at=datetime.now(UTC))
                )
            raise

        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            session.execute(
                report_export_artifact.update()
                .where(
                    report_export_artifact.c.tenant_id == actor.tenant_id,
                    report_export_artifact.c.id == export_id,
                )
                .values(
                    status="COMPLETED",
                    object_key=object_key,
                    sha256=digest,
                    size_bytes=len(payload),
                    error_code=None,
                    completed_at=datetime.now(UTC),
                )
            )
            completed = session.execute(
                select(report_export_artifact).where(report_export_artifact.c.id == export_id)
            ).mappings().one()
            return self._result(dict(completed))

    def get(self, actor: Actor, export_id: UUID) -> ExportResult:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            row = session.execute(
                select(report_export_artifact).where(
                    report_export_artifact.c.tenant_id == actor.tenant_id,
                    report_export_artifact.c.id == export_id,
                )
            ).mappings().first()
        if row is None:
            raise NotFoundError("report export was not found")
        return self._result(dict(row))

    def read_url(self, actor: Actor, export_id: UUID) -> dict[str, object]:
        result = self.get(actor, export_id)
        if result.status != "COMPLETED" or result.object_key is None:
            raise ReportExportBusyError("report export is not ready")
        observed = self._objects.head(result.object_key)
        if observed is None or observed.sha256 != result.sha256 or observed.size_bytes != result.size_bytes:
            raise ReportExportIntegrityError("export object does not match the durable catalog")
        return {
            "export_id": str(export_id),
            "sha256": result.sha256,
            "size_bytes": result.size_bytes,
            "read_url": self._objects.signed_read_url(result.object_key),
        }

    def _load_sources(self, actor: Actor, report_id: UUID, request: ExportRequest) -> list[_CanonicalReport]:
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            supervisor_row = session.execute(
                select(report).where(
                    report.c.tenant_id == actor.tenant_id,
                    report.c.id == report_id,
                    report.c.status == "COMMITTED",
                )
            ).mappings().first()
            if supervisor_row is None:
                raise NotFoundError("committed supervisor report was not found")
            rows: list[tuple[Mapping[str, Any], str | None]]
            if request.kind == "SUPERVISOR":
                rows = [(dict(supervisor_row), None)]
            elif request.kind == "SPECIALIST":
                specialist = self._specialist_row(
                    session,
                    actor,
                    UUID(str(supervisor_row["run_id"])),
                    request.agent_code,
                )
                rows = [(specialist, request.agent_code)]
            else:
                rows = [(dict(supervisor_row), None)] + [
                    (
                        self._specialist_row(session, actor, UUID(str(supervisor_row["run_id"])), agent_code),
                        agent_code,
                    )
                    for agent_code, _filename in _SPECIALISTS
                ]
        return [self._load_canonical(row, agent_code) for row, agent_code in rows]

    def _specialist_row(
        self, session: Session, actor: Actor, run_id: UUID, agent_code: str | None
    ) -> dict[str, Any]:
        row = session.execute(
            select(agent_report_artifact)
            .where(
                agent_report_artifact.c.tenant_id == actor.tenant_id,
                agent_report_artifact.c.run_id == run_id,
                agent_report_artifact.c.agent_code == agent_code,
                agent_report_artifact.c.status == "AVAILABLE",
            )
            .order_by(agent_report_artifact.c.revision.desc(), agent_report_artifact.c.created_at.desc())
            .limit(1)
        ).mappings().first()
        if row is None:
            raise NotFoundError(f"specialist report {agent_code} was not found")
        return dict(row)

    def _load_canonical(self, row: Mapping[str, Any], agent_code: str | None) -> _CanonicalReport:
        object_key = str(row["object_key"])
        expected_sha256 = str(row["sha256"])
        observed = self._objects.head(object_key)
        if observed is None or observed.sha256 != expected_sha256 or observed.size_bytes > 2_000_000:
            raise ReportExportIntegrityError("canonical report object does not match its durable catalog")
        body = self._objects.get_private(object_key, max_bytes=2_000_000)
        if hashlib.sha256(body).hexdigest() != expected_sha256:
            raise ReportExportIntegrityError("canonical report body failed sha256 validation")
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReportExportIntegrityError("canonical report is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise ReportExportIntegrityError("canonical report must be a JSON object")
        report_id = UUID(str(row["id"]))
        run_id = UUID(str(row["run_id"]))
        if document.get("report_id") != str(report_id) or document.get("run_id") != str(run_id):
            raise ReportExportIntegrityError("canonical report identity does not match its catalog")
        if agent_code is not None and document.get("agent_code") != agent_code:
            raise ReportExportIntegrityError("specialist report Agent identity does not match its catalog")
        return _CanonicalReport(report_id, run_id, agent_code, object_key, expected_sha256, document)

    def _render(
        self,
        actor: Actor,
        sources: list[_CanonicalReport],
        request: ExportRequest,
    ) -> tuple[bytes, str, str]:
        if request.kind != "PACKAGE":
            source = sources[0]
            return self._renderer.render_pdf(self._target(source, request)), "application/pdf", "pdf"
        pdfs = {"项目负责人综合报告.pdf": self._renderer.render_pdf(self._target(sources[0], request))}
        for source, (_agent_code, filename) in zip(sources[1:], _SPECIALISTS, strict=True):
            pdfs[filename] = self._renderer.render_pdf(self._target(source, request))
        source_directory = self._merged_sources(sources)
        evidence_entries = self._evidence_entries(actor, sources) if request.include_evidence else []
        package = assemble_report_package(
            pdfs=pdfs,
            source_directory=source_directory,
            evidence=evidence_entries,
            include_evidence=request.include_evidence,
        )
        return package, "application/zip", "zip"

    @staticmethod
    def _target(source: _CanonicalReport, request: ExportRequest) -> PrintTarget:
        document = dict(source.document)
        if source.agent_code and "supervisor_report_id" not in document:
            document["supervisor_report_id"] = ""
        return PrintTarget(
            run_id=str(source.run_id),
            report_id=str(source.report_id),
            agent_code=source.agent_code,
            source_sha256=source.sha256,
            document=document,
            view=request.view,
            locale=request.locale,
        )

    def _evidence_entries(self, actor: Actor, sources: list[_CanonicalReport]) -> list[EvidenceArchiveEntry]:
        citation_ids: dict[UUID, set[str]] = {}
        for source in sources:
            citations = source.document.get("citations", [])
            if not isinstance(citations, list):
                continue
            for citation in citations:
                if not isinstance(citation, dict) or citation.get("audit_status") != "VERIFIED":
                    continue
                try:
                    evidence_id = UUID(str(citation["evidence_id"]))
                except (KeyError, ValueError):
                    continue
                citation_ids.setdefault(evidence_id, set()).add(str(citation.get("citation_id", "")))
        if not citation_ids:
            return []
        run_id = sources[0].run_id
        with tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id) as session:
            rows = {
                UUID(str(row["id"])): row
                for row in session.execute(
                    select(evidence).where(
                        evidence.c.tenant_id == actor.tenant_id,
                        evidence.c.run_id == run_id,
                        evidence.c.id.in_(list(citation_ids)),
                    )
                ).mappings()
            }
        entries: list[EvidenceArchiveEntry] = []
        for evidence_id, citations in sorted(citation_ids.items(), key=lambda item: str(item[0])):
            row = rows.get(evidence_id)
            if row is None:
                entries.append(
                    EvidenceArchiveEntry(
                        str(evidence_id), f"{evidence_id}.bin", "0" * 64, None, None,
                        "CATALOG_NOT_FOUND", tuple(sorted(citations)),
                    )
                )
                continue
            key, expected = str(row["object_key"]), str(row["sha256"])
            observed = self._objects.head(key)
            body = None
            actual = None
            missing_reason = None
            if observed is None:
                missing_reason = "OBJECT_NOT_FOUND"
            elif observed.sha256 != expected:
                actual = str(observed.sha256)
                missing_reason = "SHA256_MISMATCH"
            elif observed.size_bytes > 20 * 1024 * 1024:
                missing_reason = "OBJECT_TOO_LARGE"
            else:
                body = self._objects.get_private(key, max_bytes=20 * 1024 * 1024)
                actual = hashlib.sha256(body).hexdigest()
                if actual != expected:
                    body = None
                    missing_reason = "SHA256_MISMATCH"
            entries.append(
                EvidenceArchiveEntry(
                    evidence_id=str(evidence_id),
                    filename=PurePosixPath(key.replace("\\", "/")).name,
                    expected_sha256=expected,
                    actual_sha256=actual,
                    body=body,
                    missing_reason=missing_reason,
                    citation_ids=tuple(sorted(citations)),
                )
            )
        return entries

    @staticmethod
    def _merged_sources(sources: list[_CanonicalReport]) -> list[dict[str, object]]:
        merged: dict[str, dict[str, object]] = {}
        for source in sources:
            directory = source.document.get("source_directory", [])
            if not isinstance(directory, list):
                continue
            for item in directory:
                if isinstance(item, dict):
                    key = str(item.get("source_locator_id", json.dumps(item, sort_keys=True)))
                    merged.setdefault(key, item)
        return [merged[key] for key in sorted(merged)]

    @staticmethod
    def _source_sha256(sources: list[_CanonicalReport]) -> str:
        if len(sources) == 1:
            return sources[0].sha256
        body = json.dumps(
            [{"report_id": str(source.report_id), "sha256": source.sha256} for source in sources],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def _request_sha256(report_id: UUID, request: ExportRequest) -> str:
        body = json.dumps(
            {"report_id": str(report_id), **asdict(request)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def _result(row: Mapping[str, Any]) -> ExportResult:
        return ExportResult(
            export_id=UUID(str(row["id"])),
            report_id=UUID(str(row["report_id"])),
            run_id=UUID(str(row["run_id"])),
            kind=str(row["kind"]),
            agent_code=str(row["agent_code"]) if row["agent_code"] is not None else None,
            view=str(row["view"]),
            locale=str(row["locale"]),
            include_evidence=bool(row["include_evidence"]),
            source_sha256=str(row["source_sha256"]),
            status=str(row["status"]),
            object_key=str(row["object_key"]) if row["object_key"] is not None else None,
            sha256=str(row["sha256"]) if row["sha256"] is not None else None,
            size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
            error_code=str(row["error_code"]) if row["error_code"] is not None else None,
        )


__all__ = [
    "ExportRequest",
    "ExportResult",
    "IdempotencyConflictError",
    "ReportExportApplication",
    "ReportExportBusyError",
    "ReportExportIntegrityError",
]
