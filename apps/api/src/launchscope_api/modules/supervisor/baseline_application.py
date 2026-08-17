"""Stable repeat-prediction baseline selection for report-v2 admission."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from launchscope_api.infrastructure.db.schema import decision, evaluation_run, report

REPORT_PROFILE_REF = "supervisor-report@2.0"
REPORT_PROFILE_V3_REF = "supervisor-report@3.0"
REPORT_STANDARD_VERSION = "2.2"


@dataclass(frozen=True, slots=True)
class BaselineCandidate:
    run_id: UUID
    status: str
    input_snapshot_sha256: str | None
    content_fingerprint_sha256: str | None
    standard_version: str
    report_profile_ref: str | None


@dataclass(frozen=True, slots=True)
class BaselineBinding:
    baseline_run_id: UUID | None
    status: str
    prior: BaselineCandidate | None


def report_v2_enabled() -> bool:
    return report_v3_enabled() or os.getenv("LAUNCHSCOPE_REPORT_V2_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def report_v3_enabled() -> bool:
    return os.getenv("LAUNCHSCOPE_REPORT_V3_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def report_profile_ref() -> str:
    return REPORT_PROFILE_V3_REF if report_v3_enabled() else REPORT_PROFILE_REF


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _normalize(value: object, *, strip_identity: bool) -> object:
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if strip_identity and (
                name == "id"
                or name.endswith("_id")
                or name in {"created_at", "updated_at", "confirmed_at", "submitted_at"}
            ):
                continue
            normalized[name] = _normalize(item, strip_identity=strip_identity)
        return normalized
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized_items = [_normalize(item, strip_identity=strip_identity) for item in value]
        return sorted(normalized_items, key=_canonical)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def input_snapshot_sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(_normalize(document, strip_identity=False))).hexdigest()


def content_fingerprint_sha256(document_without_random_ids: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(_normalize(document_without_random_ids, strip_identity=True))).hexdigest()


def baseline_status(prior_status: str | None, *, same_content: bool, standards_compatible: bool) -> str:
    if prior_status != "COMPLETED":
        return "FIRST_EVALUATION"
    if same_content:
        return "SAME_INPUT_RERUN"
    if not standards_compatible:
        return "STANDARD_CHANGED"
    return "COMPARABLE"


def bind_baseline_once(existing_baseline_run_id: UUID | None, candidate: BaselineCandidate | None) -> UUID | None:
    if existing_baseline_run_id is not None:
        return existing_baseline_run_id
    return candidate.run_id if candidate is not None and candidate.status == "COMPLETED" else None


def select_baseline(
    session: Session,
    *,
    tenant_id: UUID,
    project_id: UUID,
    candidate_content_fingerprint_sha256: str,
    candidate_standard_version: str,
    candidate_report_profile_ref: str,
) -> BaselineBinding:
    row = (
        session.execute(
            select(
                evaluation_run.c.id,
                evaluation_run.c.status,
                evaluation_run.c.input_snapshot_sha256,
                evaluation_run.c.content_fingerprint_sha256,
                evaluation_run.c.standard_version,
                evaluation_run.c.report_profile_ref,
            )
            .where(
                evaluation_run.c.tenant_id == tenant_id,
                evaluation_run.c.project_id == project_id,
                evaluation_run.c.run_kind == "FULL_EVALUATION",
                evaluation_run.c.status == "COMPLETED",
                exists(
                    select(decision.c.id).where(
                        decision.c.tenant_id == evaluation_run.c.tenant_id,
                        decision.c.run_id == evaluation_run.c.id,
                    )
                ),
                exists(
                    select(report.c.id).where(
                        report.c.tenant_id == evaluation_run.c.tenant_id,
                        report.c.run_id == evaluation_run.c.id,
                    )
                ),
            )
            .order_by(evaluation_run.c.created_at.desc(), evaluation_run.c.id.desc())
            .limit(1)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        return BaselineBinding(None, "FIRST_EVALUATION", None)
    prior = BaselineCandidate(
        run_id=row["id"],
        status=row["status"],
        input_snapshot_sha256=row["input_snapshot_sha256"],
        content_fingerprint_sha256=row["content_fingerprint_sha256"],
        standard_version=row["standard_version"],
        report_profile_ref=row["report_profile_ref"],
    )
    same_content = prior.content_fingerprint_sha256 == candidate_content_fingerprint_sha256
    standards_compatible = (
        prior.standard_version == candidate_standard_version
        and prior.report_profile_ref == candidate_report_profile_ref
        and prior.input_snapshot_sha256 is not None
        and prior.content_fingerprint_sha256 is not None
    )
    return BaselineBinding(
        prior.run_id,
        baseline_status(prior.status, same_content=same_content, standards_compatible=standards_compatible),
        prior,
    )


__all__ = [
    "REPORT_PROFILE_REF",
    "REPORT_PROFILE_V3_REF",
    "REPORT_STANDARD_VERSION",
    "BaselineBinding",
    "BaselineCandidate",
    "baseline_status",
    "bind_baseline_once",
    "content_fingerprint_sha256",
    "input_snapshot_sha256",
    "report_v2_enabled",
    "report_v3_enabled",
    "report_profile_ref",
    "select_baseline",
]
