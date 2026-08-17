"""Durable user-validation execution, evidence registration, and audit slices."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from launchscope_api.infrastructure.db.schema import (
    evaluation_run,
    evidence,
    evidence_audit,
    finding,
    finding_evidence,
    material,
    product_profile,
    product_version,
    project,
    run_manifest,
    run_status_history,
    skill_execution,
    skill_execution_step,
    skill_result,
    skill_result_evidence,
    task,
    user_evidence_metadata,
    user_validation_script,
    workspace_member,
)
from launchscope_api.infrastructure.db.session import tenant_transaction
from launchscope_api.modules.evidence.source_locator import (
    SourceLocatorRepository,
    internal_material_source_locator,
)
from launchscope_api.modules.identity_tenant.application import Actor, AuthorizationError, NotFoundError
from launchscope_domain.value_objects import TenantScope

from .runner import UserValidationRunner

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PII = re.compile(
    r"(?:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|"
    r"(?<!\d)\d{17}[\dXx](?!\d)|(?:api[_-]?key|secret|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_UVD_VERSION = "1.0.5"
_PRESENTATION_VERSION = "0.4"
_RUNNER_HASH = "f0923fd01aa203217b85d1c6683dc7783cf1af10019f13302dadda13d37b10f0"
_KNOWLEDGE_HASH = "d5951922224c9d16e9b013139795d074c706c3f589f8ffec918c499e910300d2"
_PROMPT_HASH = "a46381cbe819f6e09ae7df196295989bd4b3261470474be201497debd2e341a2"
_MAX_REPORT_CONTENT_BYTES = 1_048_576


class PrivateObjectStore(Protocol):
    def put_private(self, object_key: str, payload: bytes, mime_type: str) -> str: ...

    def get_private(self, object_key: str, *, max_bytes: int = 2_000_000) -> bytes: ...

    def head(self, object_key: str) -> Any: ...

    def signed_read_url(self, object_key: str) -> str: ...


class IdempotencyConflictError(ValueError):
    pass


class ObjectWriteUnknownError(RuntimeError):
    pass


class ArtifactIntegrityError(ValueError):
    pass


class ReportTooLargeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionResponse:
    payload: dict[str, object]


def _canonical_json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_canonical_json_value,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, fallback: str | None = None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected a string value")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("expected an integer value")
    return int(value)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("expected a timestamp value")
    return value


def _reject_pii(value: object) -> None:
    if _PII.search(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)):
        raise ValueError("PII or secret-like content is not allowed in user-validation inputs")


def _task_script_hash(tasks: list[dict[str, object]]) -> str:
    canonical: list[dict[str, str]] = []
    for item in tasks:
        entry: dict[str, str] = {}
        for field in ("task_key", "description", "expected_observable_outcome"):
            raw = item.get(field)
            if not isinstance(raw, str):
                raise ValueError(f"{field} must be a string")
            normalized = " ".join(unicodedata.normalize("NFC", raw).strip().split())
            if not normalized:
                raise ValueError(f"{field} cannot be empty")
            entry[field] = normalized
        canonical.append(entry)
    canonical.sort(key=lambda item: item["task_key"])
    if len({item["task_key"] for item in canonical}) != len(canonical):
        raise ValueError("product validation task keys must be unique")
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class UserValidationApplication:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        objects: PrivateObjectStore,
        runner: UserValidationRunner,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._runner = runner

    def put_script(
        self,
        actor: Actor,
        product_version_id: UUID,
        tasks: list[dict[str, object]],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> dict[str, object]:
        if not 1 <= len(tasks) <= 5:
            raise ValueError("a Product Validation Script requires one to five tasks")
        if not idempotency_key.strip() or not correlation_id.strip():
            raise ValueError("Idempotency-Key and X-Correlation-Id are required")
        for item in tasks:
            max_steps = item.get("max_steps")
            if max_steps is not None and (not isinstance(max_steps, int) or not 1 <= max_steps <= 100):
                raise ValueError("task max_steps must be between one and one hundred")
        _reject_pii(tasks)
        script_hash = _task_script_hash(tasks)
        request_hash = _digest({"tasks": tasks})
        body = _canonical({"schema_version": "1.0", "product_tasks_hash": script_hash, "tasks": tasks})
        object_hash = hashlib.sha256(body).hexdigest()
        object_key = f"tenants/{actor.tenant_id}/user-validation/scripts/{object_hash}.json"
        now = datetime.now(UTC)
        with self._transaction(actor) as session:
            version = self._version(session, actor, product_version_id, write=True, lock=True)
            existing = session.execute(
                select(user_validation_script).where(
                    user_validation_script.c.tenant_id == actor.tenant_id,
                    user_validation_script.c.product_version_id == product_version_id,
                    user_validation_script.c.idempotency_key == idempotency_key,
                )
            ).mappings().first()
            if existing is not None:
                if existing["request_sha256"] != request_hash:
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                return self._script_view(dict(existing), script_hash)
            written = self._objects.put_private(object_key, body, "application/json")
            if written != object_hash:
                raise ObjectWriteUnknownError("Product Validation Script object write has an unknown digest")
            revision = int(session.execute(
                select(func.coalesce(func.max(user_validation_script.c.revision), 0)).where(
                    user_validation_script.c.tenant_id == actor.tenant_id,
                    user_validation_script.c.product_version_id == product_version_id,
                )
            ).scalar_one()) + 1
            script_id = uuid4()
            session.execute(user_validation_script.insert().values(
                id=script_id,
                tenant_id=actor.tenant_id,
                product_version_id=version["id"],
                revision=revision,
                object_key=object_key,
                sha256=object_hash,
                product_tasks_sha256=script_hash,
                task_count=len(tasks),
                confirmed_by=actor.actor_id,
                idempotency_key=idempotency_key,
                request_sha256=request_hash,
                confirmed_at=now,
                created_at=now,
            ))
            return {
                "script_id": str(script_id),
                "product_version_id": str(product_version_id),
                "revision": revision,
                "sha256": object_hash,
                "product_tasks_hash": script_hash,
                "task_count": len(tasks),
            }

    def register_evidence(
        self,
        actor: Actor,
        product_version_id: UUID,
        payload: dict[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> dict[str, object]:
        if not idempotency_key.strip() or not correlation_id.strip():
            raise ValueError("Idempotency-Key and X-Correlation-Id are required")
        _reject_pii(payload)
        object_key = str(payload.get("object_key") or "").strip()
        object_sha = str(payload.get("sha256") or "").strip().lower()
        if not object_key or _SHA256.fullmatch(object_sha) is None:
            raise ValueError("evidence requires an exact private object key and sha256")
        tenant_prefixes = (f"tenant/{actor.tenant_id}/", f"tenants/{actor.tenant_id}/")
        if not object_key.startswith(tenant_prefixes):
            raise AuthorizationError("evidence object is outside the authenticated tenant prefix")
        metadata = self._objects.head(object_key)
        if metadata is None or metadata.sha256 != object_sha:
            raise ValueError("evidence object is missing or its sha256 does not match")
        if metadata.mime_type not in {"application/json", "text/plain", "text/csv", "application/csv"}:
            raise ValueError("user evidence must be a UTF-8 JSON, CSV, or text aggregate that can be scanned for PII")
        try:
            aggregate_body = self._objects.get_private(object_key, max_bytes=2_000_000)
            aggregate_text = aggregate_body.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("user evidence must be a UTF-8 aggregate no larger than two megabytes") from exc
        if hashlib.sha256(aggregate_body).hexdigest() != object_sha:
            raise ValueError("evidence object content does not match its registered sha256")
        _reject_pii(aggregate_text)
        request_hash = _digest({"payload": payload})
        now = datetime.now(UTC)
        with self._transaction(actor) as session:
            self._version(session, actor, product_version_id, write=True, lock=False)
            existing = session.execute(select(user_evidence_metadata).where(
                user_evidence_metadata.c.tenant_id == actor.tenant_id,
                user_evidence_metadata.c.product_version_id == product_version_id,
                user_evidence_metadata.c.idempotency_key == idempotency_key,
            )).mappings().first()
            if existing is not None:
                if existing["request_sha256"] != request_hash:
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                return self._evidence_view(dict(existing))
            evidence_id = uuid4()
            values = {
                "id": evidence_id,
                "tenant_id": actor.tenant_id,
                "product_version_id": product_version_id,
                "object_key": object_key,
                "sha256": object_sha,
                "kind": payload.get("kind"),
                "claimed_tier": payload.get("claimed_tier"),
                "source_tier": payload.get("source_tier"),
                "source": payload.get("source"),
                "observed_at": payload.get("observed_at"),
                "expires_at": payload.get("expires_at"),
                "sample_size": payload.get("sample_size"),
                "segment": payload.get("segment"),
                "aggregate_observation": payload.get("aggregate_observation"),
                "applicability": payload.get("applicability") or {},
                "supporting_claim_refs": payload.get("supporting_claim_refs") or [],
                "contradicting_claim_refs": payload.get("contradicting_claim_refs") or [],
                "idempotency_key": idempotency_key,
                "request_sha256": request_hash,
                "created_by": actor.actor_id,
                "created_at": now,
            }
            self._validate_evidence(values)
            session.execute(user_evidence_metadata.insert().values(**values))
            return self._evidence_view(values)

    def start(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        *,
        expected_revision: int,
        checkpoint_sha256: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> dict[str, object]:
        if expected_revision != 0:
            raise ValueError("a new user-validation execution must start at revision zero")
        request_hash = _digest({
            "run_id": str(run_id), "task_id": str(task_id), "expected_revision": expected_revision,
            "checkpoint_sha256": checkpoint_sha256,
        })
        with self._transaction(actor) as session:
            task_row, run_row = self._task(session, actor, run_id, task_id, expected_agent="user-evidence")
            manifest = session.execute(select(run_manifest).where(
                run_manifest.c.tenant_id == actor.tenant_id, run_manifest.c.run_id == run_id,
            )).mappings().one()
            if checkpoint_sha256 != manifest["manifest_sha256"]:
                raise ValueError("start checkpoint must bind the immutable RunManifest hash")
            existing = session.execute(select(skill_execution).where(
                skill_execution.c.tenant_id == actor.tenant_id,
                skill_execution.c.task_id == task_id,
            )).mappings().first()
            if existing is not None:
                if existing["request_sha256"] != request_hash or existing["idempotency_key"] != idempotency_key:
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                return self._execution_view(session, dict(existing))
            execution_input, mode, script_row = self._build_input(session, actor, task_row, run_row)
            policy = manifest["frozen_config"].get("user_validation", {})
            if (
                policy.get("enabled") is not True
                or policy.get("mode") != mode
                or policy.get("skill_version") != _UVD_VERSION
                or task_row["skill_version"] != _UVD_VERSION
                or policy.get("runner_sha256") != _RUNNER_HASH
                or policy.get("prompt_sha256") != _PROMPT_HASH
                or policy.get("knowledge_package_sha256") != _KNOWLEDGE_HASH
            ):
                raise ValueError("RunManifest user-validation mode does not match the durable baseline")
        _reject_pii(execution_input)
        response = self._runner.invoke({"action": "start", "input": execution_input})
        execution_id = uuid4()
        try:
            checkpoint_key, checkpoint_hash = self._write_checkpoint(actor, execution_id, response)
        except ObjectWriteUnknownError:
            self._needs_attention(actor, run_id, task_id, "OBJECT_WRITE_UNKNOWN", "UVD checkpoint write is unknown")
            raise
        now = datetime.now(UTC)
        terminal_error: ObjectWriteUnknownError | None = None
        with self._transaction(actor) as session:
            self._task(session, actor, run_id, task_id, expected_agent="user-evidence")
            current_step = self._step_id(response)
            status = self._execution_status(response)
            session.execute(skill_execution.insert().values(
                id=execution_id,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                task_id=task_id,
                skill_code="user-validation-designer",
                skill_version=_UVD_VERSION,
                mode=mode,
                status=status,
                current_step=current_step,
                revision=_integer(response["revision"]),
                checkpoint_object_key=checkpoint_key,
                checkpoint_sha256=checkpoint_hash,
                idempotency_key=idempotency_key,
                request_sha256=request_hash,
                created_at=now,
                updated_at=now,
            ))
            if response.get("status") == "completed":
                try:
                    self._persist_result(session, actor, execution_id, run_id, task_id, mode, script_row, response)
                except ObjectWriteUnknownError as exc:
                    terminal_error = exc
                    session.execute(update(skill_execution).where(
                        skill_execution.c.tenant_id == actor.tenant_id,
                        skill_execution.c.id == execution_id,
                    ).values(status="NEEDS_ATTENTION", last_error_code="OBJECT_WRITE_UNKNOWN", updated_at=now))
                    self._attention_in_session(
                        session, actor.tenant_id, run_id, task_id, "OBJECT_WRITE_UNKNOWN", "UVD result write is unknown"
                    )
            row = session.execute(select(skill_execution).where(skill_execution.c.id == execution_id)).mappings().one()
            result_payload = self._execution_view(session, dict(row), response=response)
        if terminal_error is not None:
            raise terminal_error
        return result_payload

    def submit_step(
        self,
        actor: Actor,
        execution_id: UUID,
        *,
        expected_revision: int,
        checkpoint_sha256: str,
        step_id: str,
        attempt: int,
        output: dict[str, object],
        idempotency_key: str,
        correlation_id: str,
    ) -> dict[str, object]:
        _reject_pii(output)
        request_hash = _digest({
            "execution_id": str(execution_id), "expected_revision": expected_revision,
            "checkpoint_sha256": checkpoint_sha256, "step_id": step_id, "attempt": attempt,
            "output": output,
        })
        with self._transaction(actor) as session:
            row = self._execution(session, actor, execution_id, lock=True)
            self._task(
                session,
                actor,
                _uuid(row["run_id"]),
                _uuid(row["task_id"]),
                expected_agent="user-evidence",
            )
            replay = session.execute(select(skill_execution_step).where(
                skill_execution_step.c.tenant_id == actor.tenant_id,
                skill_execution_step.c.execution_id == execution_id,
                skill_execution_step.c.idempotency_key == idempotency_key,
            )).mappings().first()
            if replay is not None:
                if replay["input_sha256"] != request_hash:
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                return self._execution_view(session, row)
            completed_attempt = session.execute(select(skill_execution_step).where(
                skill_execution_step.c.tenant_id == actor.tenant_id,
                skill_execution_step.c.execution_id == execution_id,
                skill_execution_step.c.step_id == step_id,
                skill_execution_step.c.attempt == attempt,
            )).mappings().first()
            if completed_attempt is not None:
                if completed_attempt["input_sha256"] != request_hash:
                    raise IdempotencyConflictError("execution step attempt already has different content")
                return self._execution_view(session, row)
            if row["status"] != "AWAITING_STEP":
                raise ValueError("execution is already terminal")
            if row["revision"] != expected_revision or row["checkpoint_sha256"] != checkpoint_sha256:
                raise ValueError("execution revision or checkpoint hash has changed")
            checkpoint = json.loads(
                self._objects.get_private(_string(row["checkpoint_object_key"]), max_bytes=2_000_000)
            )
        response = self._runner.invoke({
            "action": "submit", "checkpoint": checkpoint, "expected_revision": expected_revision,
            "checkpoint_hash": checkpoint_sha256, "step_id": step_id, "attempt": attempt, "output": output,
        })
        output_body = _canonical(output)
        output_hash = hashlib.sha256(output_body).hexdigest()
        output_key = (
            f"tenants/{actor.tenant_id}/user-validation/executions/{execution_id}/steps/"
            f"{step_id}-{attempt}-{output_hash}.json"
        )
        try:
            written_output_hash = self._objects.put_private(output_key, output_body, "application/json")
        except Exception as exc:
            self._needs_attention(
                actor, row["run_id"], row["task_id"], "OBJECT_WRITE_UNKNOWN", "UVD step object write is unknown"
            )
            raise ObjectWriteUnknownError("UVD step object write completion is unknown") from exc
        if written_output_hash != output_hash:
            self._needs_attention(
                actor, row["run_id"], row["task_id"], "OBJECT_WRITE_UNKNOWN", "UVD step object write is unknown"
            )
            raise ObjectWriteUnknownError("UVD step object write has an unknown digest")
        try:
            checkpoint_key, next_hash = self._write_checkpoint(actor, execution_id, response)
        except ObjectWriteUnknownError:
            self._needs_attention(
                actor, row["run_id"], row["task_id"], "OBJECT_WRITE_UNKNOWN", "UVD checkpoint write is unknown"
            )
            raise
        now = datetime.now(UTC)
        terminal_error: ObjectWriteUnknownError | None = None
        with self._transaction(actor) as session:
            current = self._execution(session, actor, execution_id, lock=True)
            if current["revision"] != expected_revision or current["checkpoint_sha256"] != checkpoint_sha256:
                raise IdempotencyConflictError("execution advanced concurrently")
            session.execute(skill_execution_step.insert().values(
                id=uuid4(), tenant_id=actor.tenant_id, execution_id=execution_id,
                step_id=step_id, attempt=attempt, revision=_integer(response["revision"]),
                idempotency_key=idempotency_key,
                input_sha256=request_hash, output_object_key=output_key, output_sha256=output_hash,
                status="ACCEPTED", created_at=now,
            ))
            status = self._execution_status(response)
            session.execute(update(skill_execution).where(
                skill_execution.c.tenant_id == actor.tenant_id, skill_execution.c.id == execution_id,
            ).values(
                status=status, current_step=self._step_id(response), revision=_integer(response["revision"]),
                checkpoint_object_key=checkpoint_key, checkpoint_sha256=next_hash, updated_at=now,
            ))
            if response.get("status") == "completed":
                current_run_id = _uuid(current["run_id"])
                current_task_id = _uuid(current["task_id"])
                current_mode = _string(current["mode"])
                script_row = self._latest_script(session, actor, current_run_id)
                try:
                    self._persist_result(
                        session, actor, execution_id, current_run_id, current_task_id, current_mode,
                        script_row, response,
                    )
                except ObjectWriteUnknownError as exc:
                    terminal_error = exc
                    session.execute(update(skill_execution).where(
                        skill_execution.c.tenant_id == actor.tenant_id,
                        skill_execution.c.id == execution_id,
                    ).values(status="NEEDS_ATTENTION", last_error_code="OBJECT_WRITE_UNKNOWN", updated_at=now))
                    self._attention_in_session(
                        session,
                        actor.tenant_id,
                        current_run_id,
                        current_task_id,
                        "OBJECT_WRITE_UNKNOWN",
                        "UVD result write is unknown",
                    )
            refreshed = (
                session.execute(select(skill_execution).where(skill_execution.c.id == execution_id)).mappings().one()
            )
            result_payload = self._execution_view(session, dict(refreshed), response=response)
        if terminal_error is not None:
            raise terminal_error
        return result_payload

    def resume(
        self,
        actor: Actor,
        execution_id: UUID,
        *,
        expected_revision: int,
        checkpoint_sha256: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> dict[str, object]:
        if not idempotency_key.strip() or not correlation_id.strip():
            raise ValueError("Idempotency-Key and X-Correlation-Id are required")
        with self._transaction(actor) as session:
            row = self._execution(session, actor, execution_id, lock=False)
            self._task(
                session,
                actor,
                _uuid(row["run_id"]),
                _uuid(row["task_id"]),
                expected_agent="user-evidence",
            )
            if row["revision"] != expected_revision or row["checkpoint_sha256"] != checkpoint_sha256:
                raise ValueError("execution revision or checkpoint hash has changed")
            if row["status"] == "COMPLETED":
                return self._execution_view(session, row)
            checkpoint = json.loads(
                self._objects.get_private(_string(row["checkpoint_object_key"]), max_bytes=2_000_000)
            )
        response = self._runner.invoke({
            "action": "resume", "checkpoint": checkpoint,
            "expected_revision": expected_revision, "checkpoint_hash": checkpoint_sha256,
        })
        return self._public_response(execution_id, response)

    def create_recheck(
        self,
        actor: Actor,
        baseline_run_id: UUID,
        *,
        idempotency_key: str,
        correlation_id: UUID,
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        with self._transaction(actor) as session:
            baseline = session.execute(select(evaluation_run).where(
                evaluation_run.c.tenant_id == actor.tenant_id,
                evaluation_run.c.id == baseline_run_id,
            ).with_for_update()).mappings().first()
            if baseline is None:
                raise NotFoundError("baseline Run was not found")
            self._version(session, actor, baseline["product_version_id"], write=True, lock=False)
            completed = session.execute(select(skill_result.c.id).where(
                skill_result.c.tenant_id == actor.tenant_id,
                skill_result.c.run_id == baseline_run_id,
                skill_result.c.status.in_(("COMPLETED", "PARTIAL")),
            )).first()
            if completed is None:
                raise ValueError("baseline Run has no completed user-validation result")
            existing = session.execute(select(evaluation_run).where(
                evaluation_run.c.tenant_id == actor.tenant_id,
                evaluation_run.c.idempotency_key == idempotency_key,
            )).mappings().first()
            if existing is not None:
                if existing["baseline_run_id"] != baseline_run_id:
                    raise IdempotencyConflictError("IDEMPOTENCY_CONFLICT")
                return {"run_id": str(existing["id"]), "status": existing["status"], "run_kind": existing["run_kind"]}
            run_id = uuid4()
            session.execute(evaluation_run.insert().values(
                id=run_id, tenant_id=actor.tenant_id, project_id=baseline["project_id"],
                product_version_id=baseline["product_version_id"], status="PLANNED", current_stage=None,
                state_flags={
                    "profile_confirmed": True,
                    "user_evidence_recheck": True,
                    "architecture_generation": "supervisor-1p4-v1",
                },
                standard_version=baseline["standard_version"], correlation_id=correlation_id,
                idempotency_key=idempotency_key, run_kind="USER_EVIDENCE_RECHECK",
                baseline_run_id=baseline_run_id, created_at=now, updated_at=now,
            ))
            self._inherit_audited_dimensions(session, actor.tenant_id, baseline_run_id, run_id, now)
            session.execute(run_status_history.insert().values(
                id=uuid4(), tenant_id=actor.tenant_id, run_id=run_id, from_status="PLANNED",
                to_status="PLANNED", reason=f"user evidence recheck of {baseline_run_id}", occurred_at=now,
            ))
            return {
                "run_id": str(run_id),
                "status": "PLANNED",
                "run_kind": "USER_EVIDENCE_RECHECK",
                "baseline_run_id": str(baseline_run_id),
            }

    @staticmethod
    def _inherit_audited_dimensions(
        session: Session,
        tenant_id: UUID,
        baseline_run_id: UUID,
        recheck_run_id: UUID,
        now: datetime,
    ) -> None:
        rows = session.execute(select(finding, evidence_audit).join(
            evidence_audit,
            (evidence_audit.c.tenant_id == finding.c.tenant_id)
            & (evidence_audit.c.finding_id == finding.c.id),
        ).where(
            finding.c.tenant_id == tenant_id,
            finding.c.run_id == baseline_run_id,
            finding.c.dimension_code != "USER_USAGE",
        )).mappings().all()
        for row in rows:
            source_finding_id = row[finding.c.id]
            inherited_id = uuid4()
            structured = dict(row[finding.c.structured_result])
            structured["inherited_from_run_id"] = str(baseline_run_id)
            structured["inherited_from_finding_id"] = str(source_finding_id)
            session.execute(finding.insert().values(
                id=inherited_id,
                tenant_id=tenant_id,
                run_id=recheck_run_id,
                task_id=None,
                dimension_code=row[finding.c.dimension_code],
                grade=row[finding.c.grade],
                claim_type=row[finding.c.claim_type],
                statement=row[finding.c.statement],
                is_hypothesis=row[finding.c.is_hypothesis],
                submitted_by=row[finding.c.submitted_by],
                submitted_at=now,
                supersedes_id=source_finding_id,
                structured_result=structured,
                simulated=row[finding.c.simulated],
                hard_block=row[finding.c.hard_block],
                block_reason=row[finding.c.block_reason],
            ))
            for link in session.execute(select(finding_evidence).where(
                finding_evidence.c.tenant_id == tenant_id,
                finding_evidence.c.finding_id == source_finding_id,
            )).mappings():
                session.execute(finding_evidence.insert().values(
                    tenant_id=tenant_id,
                    finding_id=inherited_id,
                    evidence_id=link["evidence_id"],
                    relation_type=link["relation_type"],
                ))
            session.execute(evidence_audit.insert().values(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=recheck_run_id,
                finding_id=inherited_id,
                decision=row[evidence_audit.c.decision],
                auditor_id=row[evidence_audit.c.auditor_id],
                reason=f"Inherited immutable baseline audit: {row[evidence_audit.c.reason]}"[:2000],
                contract_version=row[evidence_audit.c.contract_version],
                rule_ids=row[evidence_audit.c.rule_ids],
                referenced_evidence_ids=row[evidence_audit.c.referenced_evidence_ids],
                score_components=row[evidence_audit.c.score_components],
                flags=row[evidence_audit.c.flags],
                audited_at=now,
            ))

    def get_result(self, actor: Actor, run_id: UUID) -> dict[str, object]:
        with self._transaction(actor) as session:
            run = session.execute(select(evaluation_run).where(
                evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id,
            )).mappings().first()
            if run is None:
                raise NotFoundError("Run was not found")
            self._version(session, actor, run["product_version_id"], write=False, lock=False)
            result = session.execute(select(skill_result).where(
                skill_result.c.tenant_id == actor.tenant_id, skill_result.c.run_id == run_id,
            ).order_by(skill_result.c.created_at.desc())).mappings().first()
            if result is None:
                raise NotFoundError("user-validation result was not found")
            settings = getattr(self._objects, "settings", None)
            expires_in_seconds = int(getattr(settings, "presign_ttl_seconds", 900))
            summary = result["summary"] if isinstance(result["summary"], dict) else {}
            presentation = summary.get("presentation")
            if not isinstance(presentation, dict) or presentation.get("version") != _PRESENTATION_VERSION:
                presentation = None
            return {
                "run_id": str(run_id), "skill_result_ref": str(result["id"]), "status": result["status"],
                "schema_version": result["schema_version"], "mode": result["mode"], "summary": result["summary"],
                "presentation": presentation, "skill_result_sha256": result["sha256"],
                "report_url": self._objects.signed_read_url(result["object_key"]),
                "expires_in_seconds": expires_in_seconds,
            }

    def get_report(
        self,
        actor: Actor,
        run_id: UUID,
        *,
        variant: str,
        report_format: str,
    ) -> dict[str, object]:
        if variant not in {"summary", "full"} or report_format not in {"html", "markdown"}:
            raise ValueError("report variant or format is invalid")
        with self._transaction(actor) as session:
            run = session.execute(select(evaluation_run).where(
                evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id,
            )).mappings().first()
            if run is None:
                raise NotFoundError("Run was not found")
            self._version(session, actor, run["product_version_id"], write=False, lock=False)
            result = session.execute(select(skill_result).where(
                skill_result.c.tenant_id == actor.tenant_id, skill_result.c.run_id == run_id,
            ).order_by(skill_result.c.created_at.desc())).mappings().first()
            if result is None:
                raise NotFoundError("user-validation result was not found")
            result_row = dict(result)
        summary = result_row["summary"] if isinstance(result_row["summary"], dict) else {}
        presentation = summary.get("presentation")
        selected = presentation.get(variant) if isinstance(presentation, dict) else None
        selected_format = selected.get(report_format) if isinstance(selected, dict) else None
        expected_hash = selected_format.get("content_sha256") if isinstance(selected_format, dict) else None
        if (
            result_row["schema_version"] != _UVD_VERSION
            or not isinstance(presentation, dict)
            or presentation.get("version") != _PRESENTATION_VERSION
            or not isinstance(selected_format, dict)
            or selected_format.get("available") is not True
            or not isinstance(expected_hash, str)
        ):
            raise NotFoundError("the requested user-validation presentation is unavailable")
        artifact = self._read_result_artifact(result_row)
        report = artifact.get("uvd_report")
        structured = report.get("structured_output") if isinstance(report, dict) else None
        field = f"{variant}_report_html" if report_format == "html" else f"{variant}_report"
        content = structured.get(field) if isinstance(structured, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise NotFoundError("the requested user-validation presentation is unavailable")
        if len(content.encode("utf-8")) > _MAX_REPORT_CONTENT_BYTES:
            raise ReportTooLargeError("the requested user-validation presentation exceeds one megabyte")
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_hash != expected_hash:
            raise ArtifactIntegrityError("user-validation presentation hash does not match durable metadata")
        return {
            "run_id": str(run_id), "skill_result_ref": str(result_row["id"]), "variant": variant,
            "format": report_format, "content": content, "content_sha256": actual_hash,
            "skill_result_sha256": result_row["sha256"],
        }

    def audit_context(
        self,
        actor: Actor,
        run_id: UUID,
        task_id: UUID,
        result_id: UUID,
        *,
        section: str = "summary",
        cursor: str | None = None,
    ) -> dict[str, object]:
        with self._transaction(actor) as session:
            self._task(session, actor, run_id, task_id, expected_agent="evidence-auditor")
            result = session.execute(select(skill_result).where(
                skill_result.c.tenant_id == actor.tenant_id,
                skill_result.c.run_id == run_id,
                skill_result.c.id == result_id,
            )).mappings().first()
            if result is None:
                raise NotFoundError("user-validation result was not found")
        artifact = self._read_result_artifact(dict(result))
        report = artifact.get("uvd_report", {})
        structured = report.get("structured_output", {}) if isinstance(report, dict) else {}
        items = self._audit_items(section, report, structured)
        offset = int(cursor or "0")
        if offset < 0 or offset > len(items):
            raise ValueError("audit context cursor is invalid")
        page: list[object] = []
        size = 0
        for item in items[offset:]:
            item_size = len(_canonical(item))
            if page and size + item_size > 56_000:
                break
            if item_size > 56_000:
                raise ValueError("one audit context item exceeds the bounded slice")
            page.append(item)
            size += item_size
        next_offset = offset + len(page)
        return {
            "skill_result_ref": str(result_id), "skill_result_sha256": result["sha256"], "section": section,
            "items": page, "next_cursor": str(next_offset) if next_offset < len(items) else None,
            "knowledge_rules": self._knowledge_rules(section),
        }

    def _build_input(
        self, session: Session, actor: Actor, task_row: Mapping[str, object], run_row: Mapping[str, object]
    ) -> tuple[dict[str, object], str, Mapping[str, object]]:
        version = self._version(session, actor, _uuid(run_row["product_version_id"]), write=False, lock=False)
        profile = session.execute(select(product_profile).where(
            product_profile.c.tenant_id == actor.tenant_id,
            product_profile.c.product_version_id == version["id"],
            product_profile.c.confirmation_status == "CONFIRMED",
        ).order_by(product_profile.c.confirmed_at.desc())).mappings().first()
        if profile is None:
            raise ValueError("a confirmed ProductProfile is required")
        fields = dict(profile["confirmed_fields"])
        value_claim = _text(fields.get("one_line_value_claim"))
        if value_claim is None:
            raise ValueError("confirmed ProductProfile requires one_line_value_claim")
        script = session.execute(select(user_validation_script).where(
            user_validation_script.c.tenant_id == actor.tenant_id,
            user_validation_script.c.product_version_id == version["id"],
        ).order_by(user_validation_script.c.revision.desc())).mappings().first()
        if script is None:
            raise ValueError("a confirmed Product Validation Script is required")
        script_document = json.loads(self._objects.get_private(script["object_key"], max_bytes=2_000_000))
        material_ids = session.execute(
            select(material.c.id).where(
                material.c.tenant_id == actor.tenant_id,
                material.c.product_version_id == version["id"],
                material.c.ingest_status == "VALIDATED",
            ).order_by(material.c.created_at).limit(20)
        ).scalars().all()
        explicit_experience_ref = _text(fields.get("experience_report_ref"))
        experience_ref = explicit_experience_ref or (
            "LaunchScope validated material refs: " + ",".join(str(value) for value in material_ids)
            if material_ids
            else None
        )
        profile_context = [
            ("description", fields.get("description")),
            ("problem", fields.get("problem")),
            ("core_features", fields.get("core_features")),
            ("region", fields.get("region")),
        ]
        profile_description = "\n".join(
            f"{label}: {value.strip()}"
            for label, value in profile_context
            if isinstance(value, str) and value.strip()
        ) or None
        records = session.execute(select(user_evidence_metadata).where(
            user_evidence_metadata.c.tenant_id == actor.tenant_id,
            user_evidence_metadata.c.product_version_id == version["id"],
        ).order_by(user_evidence_metadata.c.created_at)).mappings().all()
        evidence_input = [self._evidence_input(dict(item), version["label"]) for item in records]
        mode = "first_validation"
        previous: dict[str, object] | None = None
        previous_hash: str | None = None
        if run_row.get("run_kind") == "USER_EVIDENCE_RECHECK":
            mode = "evidence_recheck"
            prior = self._result_for_run(session, actor, run_row["baseline_run_id"])
            artifact = self._read_result_artifact(prior)
            prior_report = artifact.get("uvd_report")
            if not isinstance(prior_report, dict):
                raise ValueError("baseline user-validation result lacks a native report")
            candidate = prior_report.get("structured_output")
            if not isinstance(candidate, dict):
                raise ValueError("baseline user-validation result lacks structured output")
            previous = candidate
            previous_manifest = previous.get("run_manifest") if isinstance(previous, dict) else None
            previous_hash = (
                str(previous_manifest.get("state_hash"))
                if isinstance(previous_manifest, dict) and previous_manifest.get("state_hash")
                else None
            )
        else:
            regression_prior = self._compatible_previous_result(
                session, actor, run_row["project_id"], run_row["id"], run_row["standard_version"],
                script_document["product_tasks_hash"],
            )
            if regression_prior is not None:
                mode = "version_regression"
                artifact = self._read_result_artifact(regression_prior)
                prior_report = artifact.get("uvd_report")
                if not isinstance(prior_report, dict):
                    raise ValueError("baseline user-validation result lacks a native report")
                previous = self._regression_baseline(prior_report)
                previous_hash = _digest(previous)
        result: dict[str, object] = {
            "task_id": str(task_row["id"]), "project_id": str(version["project_id"]),
            "product_version": str(version["label"]),
            "product_profile": {
                "name": version["project_name"], "one_line_value_claim": value_claim,
                "description": profile_description, "category": _text(fields.get("category")),
                "url": _text(fields.get("url")), "experience_report_ref": experience_ref,
                "pricing_claim": _text(fields.get("pricing_claim")),
            },
            "target_users": {
                "raw_description": str(fields.get("target_user") or ""),
                "segments": fields.get("target_segments") or None,
                "exclusions": fields.get("target_exclusions") or None,
                "claimed_payer": _text(fields.get("payer")), "claimed_first_user": _text(fields.get("first_user")),
            },
            "product_tasks": script_document["tasks"], "existing_user_evidence": evidence_input or None,
            "validation_goal": {"objective": str(fields.get("validation_goal") or "")},
            "product_stage": self._product_stage(fields.get("stage")),
            "constraints": fields.get("validation_constraints") or None,
            "evidence_refs": [str(item["id"]) for item in records] or None,
            "runtime": {
                "allowed_tools": ["product_reader", "simulation_engine", "evidence_writer", "kb_retriever"],
                "mode": mode, "product_tasks_hash": script_document["product_tasks_hash"],
                "scoring_schema_version": "uvd-1.0.4", "max_simulation_retries": 2,
            },
        }
        if mode == "version_regression":
            result["previous_validation_results"] = previous
            result["previous_validation_results_hash"] = previous_hash
        elif mode == "evidence_recheck":
            result["previous_structured_output"] = previous
            result["previous_state_hash"] = previous_hash
        return result, mode, dict(script)

    def _persist_result(
        self,
        session: Session,
        actor: Actor,
        execution_id: UUID,
        run_id: UUID,
        task_id: UUID,
        mode: str,
        script: Mapping[str, object],
        response: dict[str, object],
    ) -> None:
        if session.execute(select(skill_result.c.id).where(
            skill_result.c.tenant_id == actor.tenant_id, skill_result.c.execution_id == execution_id,
        )).first() is not None:
            return
        report = response.get("result")
        if not isinstance(report, dict):
            raise ValueError("terminal UVD response lacks a native report")
        presentation = self._presentation_metadata(report)
        manifest_config = session.execute(
            select(run_manifest.c.frozen_config).where(
                run_manifest.c.tenant_id == actor.tenant_id,
                run_manifest.c.run_id == run_id,
            )
        ).scalar_one()
        model_runtime = manifest_config.get("model_runtime", {}).get("user-evidence", {})
        identity_runtime = manifest_config.get("agents", {}).get("user-evidence", {})
        metadata = {
            "run_id": str(run_id), "task_id": str(task_id), "mode": mode,
            "skill": {"code": "user-validation-designer", "version": _UVD_VERSION, "runner_sha256": _RUNNER_HASH},
            "knowledge_package_sha256": _KNOWLEDGE_HASH, "prompt_sha256": _PROMPT_HASH,
            "model": {
                "model_id": model_runtime.get("model_id"),
                "configuration_sha256": model_runtime.get("configuration_sha256"),
                "agent_identity_sha256": identity_runtime.get("sha256"),
            },
            "validation_script_sha256": script["sha256"], "step_revision": response["revision"],
            "step_integrity": "complete", "uvd_report_sha256": response.get("result_sha256"),
        }
        artifact = {
            "schema_version": "launchscope.user-validation-result.v1",
            "launchscope": metadata,
            "uvd_report": report,
        }
        body = _canonical(artifact)
        digest = hashlib.sha256(body).hexdigest()
        object_key = f"tenants/{actor.tenant_id}/user-validation/results/{run_id}/{digest}.json"
        try:
            written_hash = self._objects.put_private(object_key, body, "application/json")
        except Exception as exc:
            raise ObjectWriteUnknownError("UVD result object write completion is unknown") from exc
        if written_hash != digest:
            raise ObjectWriteUnknownError("UVD result object write has an unknown digest")
        result_id = uuid4()
        native_status = str(report.get("status") or "failed").upper()
        summary = {
            "result_summary": report.get("result_summary"), "confidence": report.get("confidence"),
            "risks": report.get("risks") or [], "needs_human_review": report.get("needs_human_review", True),
            "validation_script_sha256": script["sha256"], "product_tasks_hash": self._script_task_hash(script),
            "preliminary": self._is_preliminary(report),
            "presentation": presentation,
        }
        session.execute(skill_result.insert().values(
            id=result_id, tenant_id=actor.tenant_id, execution_id=execution_id, run_id=run_id, task_id=task_id,
            schema_version=_UVD_VERSION, mode=mode, status=native_status, object_key=object_key, sha256=digest,
            size_bytes=len(body), summary=summary, created_at=datetime.now(UTC),
        ))
        self._persist_evidence(session, actor, result_id, run_id, task_id, report, object_key, digest, len(body))

    def _persist_evidence(
        self,
        session: Session,
        actor: Actor,
        result_id: UUID,
        run_id: UUID,
        task_id: UUID,
        report: dict[str, object],
        object_key: str,
        report_sha: str,
        size_bytes: int,
    ) -> None:
        structured = report.get("structured_output")
        cards = structured.get("evidence_cards", []) if isinstance(structured, dict) else []
        for card in cards if isinstance(cards, list) else []:
            if not isinstance(card, dict):
                continue
            level = str(card.get("reliability_level") or card.get("evidence_level") or "E0")
            if level not in {"E0", "E1", "E2"}:
                level = "E2"
            evidence_id = uuid4()
            external_id = str(card.get("evidence_id") or evidence_id)
            created_at = datetime.now(UTC)
            session.execute(evidence.insert().values(
                id=evidence_id, tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id, material_id=None,
                source_type="DERIVED", object_key=object_key, sha256=report_sha, size_bytes=size_bytes,
                mime_type="application/json", evidence_level=level, trust_level=level,
                summary=str(card.get("observation") or card.get("claim") or "UVD simulated evidence")[:4000],
                published_at=None, fetched_at=created_at, valid_from=None, valid_until=None,
                region=None, simulated=True, supersedes_id=None, created_at=created_at,
            ))
            SourceLocatorRepository().append(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                evidence_id=evidence_id,
                locators=(
                    internal_material_source_locator(
                        display_name=f"用户验证模拟证据 {external_id}",
                        fetched_at=created_at,
                        content_sha256=report_sha,
                        locator={"evidence_card_id": external_id},
                    ),
                ),
            )
            session.execute(skill_result_evidence.insert().values(
                tenant_id=actor.tenant_id, skill_result_id=result_id, evidence_id=evidence_id,
                external_evidence_id=external_id[:255], origin="skill_issued", created_at=created_at,
            ))
        external_refs = report.get("evidence_refs")
        for external_id in external_refs if isinstance(external_refs, list) else []:
            try:
                metadata_id = UUID(str(external_id))
            except ValueError:
                continue
            record = session.execute(select(user_evidence_metadata).where(
                user_evidence_metadata.c.tenant_id == actor.tenant_id,
                user_evidence_metadata.c.id == metadata_id,
            )).mappings().first()
            if record is None:
                continue
            evidence_id = uuid4()
            session.execute(evidence.insert().values(
                id=evidence_id, tenant_id=actor.tenant_id, run_id=run_id, task_id=task_id, material_id=None,
                source_type="MATERIAL", object_key=record["object_key"], sha256=record["sha256"],
                size_bytes=self._object_size(record["object_key"]), mime_type="application/json",
                evidence_level=record["claimed_tier"], trust_level=record["claimed_tier"],
                summary=record["aggregate_observation"], published_at=record["observed_at"],
                fetched_at=record["created_at"], valid_from=record["observed_at"], valid_until=record["expires_at"],
                region=None, simulated=False, supersedes_id=None, created_at=datetime.now(UTC),
            ))
            SourceLocatorRepository().append(
                session,
                tenant_id=actor.tenant_id,
                run_id=run_id,
                evidence_id=evidence_id,
                locators=(
                    internal_material_source_locator(
                        display_name=str(record["source"]),
                        fetched_at=record["created_at"],
                        content_sha256=record["sha256"],
                        locator={
                            "kind": record["kind"],
                            "sample_size": record["sample_size"],
                            "segment": record["segment"],
                        },
                    ),
                ),
            )
            session.execute(skill_result_evidence.insert().values(
                tenant_id=actor.tenant_id, skill_result_id=result_id, evidence_id=evidence_id,
                external_evidence_id=str(metadata_id), origin="caller_supplied", created_at=datetime.now(UTC),
            ))

    def _write_checkpoint(
        self, actor: Actor, execution_id: UUID, response: dict[str, object]
    ) -> tuple[str, str]:
        checkpoint = response.get("checkpoint")
        if not isinstance(checkpoint, dict):
            raise ValueError("runner response lacks a checkpoint")
        body = _canonical(checkpoint)
        digest = hashlib.sha256(body).hexdigest()
        runner_hash = str(response.get("checkpoint_hash") or "")
        if runner_hash != digest:
            raise ValueError("runner checkpoint hash differs from canonical checkpoint bytes")
        revision = _integer(response.get("revision") or 0)
        key = (
            f"tenants/{actor.tenant_id}/user-validation/executions/{execution_id}/checkpoints/"
            f"{revision}-{digest}.json"
        )
        try:
            written_hash = self._objects.put_private(key, body, "application/json")
        except Exception as exc:
            raise ObjectWriteUnknownError("UVD checkpoint object write completion is unknown") from exc
        if written_hash != digest:
            raise ObjectWriteUnknownError("UVD checkpoint object write has an unknown digest")
        return key, digest

    def _execution_view(
        self, session: Session, row: Mapping[str, object], *, response: dict[str, object] | None = None
    ) -> dict[str, object]:
        if response is None and row["status"] == "AWAITING_STEP":
            checkpoint = json.loads(
                self._objects.get_private(_string(row["checkpoint_object_key"]), max_bytes=2_000_000)
            )
            response = self._runner.invoke({
                "action": "resume", "checkpoint": checkpoint,
                "expected_revision": row["revision"], "checkpoint_hash": row["checkpoint_sha256"],
            })
        result = session.execute(select(skill_result).where(
            skill_result.c.tenant_id == row["tenant_id"], skill_result.c.execution_id == row["id"],
        )).mappings().first()
        if response is not None:
            response_payload = self._public_response(_uuid(row["id"]), response)
            if result is not None:
                evidence_ids = session.execute(select(skill_result_evidence.c.evidence_id).where(
                    skill_result_evidence.c.tenant_id == row["tenant_id"],
                    skill_result_evidence.c.skill_result_id == result["id"],
                )).scalars().all()
                response_payload.update({
                    "skill_result_ref": str(result["id"]),
                    "skill_result_sha256": result["sha256"],
                    "validation_mode": result["mode"],
                    "evidence_refs": [str(value) for value in evidence_ids],
                    "summary": result["summary"],
                })
            return response_payload
        payload: dict[str, object] = {
            "execution_id": str(row["id"]), "status": str(row["status"]).lower(),
            "revision": row["revision"], "checkpoint_sha256": row["checkpoint_sha256"],
            "current_step": row["current_step"], "mode": row["mode"],
        }
        if result is not None:
            evidence_ids = session.execute(select(skill_result_evidence.c.evidence_id).where(
                skill_result_evidence.c.tenant_id == row["tenant_id"],
                skill_result_evidence.c.skill_result_id == result["id"],
            )).scalars().all()
            payload.update({
                "skill_result_ref": str(result["id"]),
                "skill_result_sha256": result["sha256"],
                "validation_mode": result["mode"],
                "evidence_refs": [str(value) for value in evidence_ids],
                "summary": result["summary"],
            })
        return payload

    @staticmethod
    def _public_response(execution_id: UUID, response: dict[str, object]) -> dict[str, object]:
        payload: dict[str, object] = {
            "execution_id": str(execution_id), "status": response["status"], "revision": response["revision"],
            "checkpoint_sha256": response["checkpoint_hash"],
        }
        if response.get("status") == "awaiting_step":
            payload["step"] = response["step"]
        else:
            report = response.get("result")
            if not isinstance(report, dict):
                return payload
            payload["result_status"] = report.get("status")
            payload["result_summary"] = report.get("result_summary")
        return payload

    def _execution(self, session: Session, actor: Actor, execution_id: UUID, *, lock: bool) -> Mapping[str, object]:
        query = select(skill_execution).where(
            skill_execution.c.tenant_id == actor.tenant_id, skill_execution.c.id == execution_id,
        )
        if lock:
            query = query.with_for_update()
        row = session.execute(query).mappings().first()
        if row is None:
            raise NotFoundError("user-validation execution was not found")
        return dict(row)

    def _task(
        self, session: Session, actor: Actor, run_id: UUID, task_id: UUID, *, expected_agent: str
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        row = session.execute(select(task, evaluation_run).join(
            evaluation_run,
            (evaluation_run.c.tenant_id == task.c.tenant_id) & (evaluation_run.c.id == task.c.run_id),
        ).where(
            task.c.tenant_id == actor.tenant_id, task.c.run_id == run_id, task.c.id == task_id,
        )).mappings().first()
        if row is None:
            raise NotFoundError("Task was not found")
        agent_code = str(row["agent_identity_ref"]).split("@", 1)[0]
        if agent_code != expected_agent:
            raise AuthorizationError(f"only {expected_agent} may use this capability")
        run_values = {column.name: row[column] for column in evaluation_run.columns}
        task_values = {column.name: row[column] for column in task.columns}
        return task_values, run_values

    def _version(
        self, session: Session, actor: Actor, version_id: UUID, *, write: bool, lock: bool
    ) -> Mapping[str, object]:
        query = select(
            product_version.c.id, product_version.c.project_id, product_version.c.label,
            product_version.c.version_number, project.c.name.label("project_name"), project.c.workspace_id,
            workspace_member.c.role,
        ).join(
            project,
            (project.c.tenant_id == product_version.c.tenant_id) & (project.c.id == product_version.c.project_id),
        ).outerjoin(
            workspace_member,
            (workspace_member.c.tenant_id == project.c.tenant_id)
            & (workspace_member.c.workspace_id == project.c.workspace_id)
            & (workspace_member.c.actor_id == actor.actor_id),
        ).where(product_version.c.tenant_id == actor.tenant_id, product_version.c.id == version_id)
        if lock:
            query = query.with_for_update(of=product_version)
        row = session.execute(query).mappings().first()
        if row is None:
            raise NotFoundError("product version was not found")
        allowed = {"OWNER", "EDITOR"} if write else {"OWNER", "EDITOR", "VIEWER"}
        if row["role"] not in allowed:
            raise AuthorizationError("actor lacks the required workspace role")
        return dict(row)

    def _latest_script(self, session: Session, actor: Actor, run_id: UUID) -> Mapping[str, object]:
        product_version_id = session.execute(select(evaluation_run.c.product_version_id).where(
            evaluation_run.c.tenant_id == actor.tenant_id, evaluation_run.c.id == run_id,
        )).scalar_one()
        row = session.execute(select(user_validation_script).where(
            user_validation_script.c.tenant_id == actor.tenant_id,
            user_validation_script.c.product_version_id == product_version_id,
        ).order_by(user_validation_script.c.revision.desc())).mappings().first()
        if row is None:
            raise ValueError("Product Validation Script was not found")
        return dict(row)

    def _result_for_run(self, session: Session, actor: Actor, run_id: object) -> Mapping[str, object]:
        row = session.execute(select(skill_result).where(
            skill_result.c.tenant_id == actor.tenant_id, skill_result.c.run_id == run_id,
            skill_result.c.status.in_(("COMPLETED", "PARTIAL")),
        ).order_by(skill_result.c.created_at.desc())).mappings().first()
        if row is None:
            raise ValueError("baseline user-validation result is unavailable")
        return dict(row)

    def _compatible_previous_result(
        self,
        session: Session,
        actor: Actor,
        project_id: object,
        current_run_id: object,
        standard_version: object,
        task_hash: str,
    ) -> Mapping[str, object] | None:
        rows = session.execute(select(skill_result, evaluation_run.c.standard_version).join(
            evaluation_run,
            (evaluation_run.c.tenant_id == skill_result.c.tenant_id) & (evaluation_run.c.id == skill_result.c.run_id),
        ).where(
            skill_result.c.tenant_id == actor.tenant_id,
            evaluation_run.c.project_id == project_id,
            evaluation_run.c.id != current_run_id,
            skill_result.c.status.in_(("COMPLETED", "PARTIAL")),
        ).order_by(skill_result.c.created_at.desc())).mappings().all()
        for row in rows:
            summary = row["summary"] if isinstance(row["summary"], dict) else {}
            if row["standard_version"] == standard_version and summary.get("product_tasks_hash") == task_hash:
                return dict(row)
        return None

    @staticmethod
    def _regression_baseline(report: dict[str, object]) -> dict[str, object]:
        structured_value = report.get("structured_output")
        structured = structured_value if isinstance(structured_value, dict) else {}
        manifest_value = structured.get("run_manifest")
        manifest = manifest_value if isinstance(manifest_value, dict) else {}
        raw_hypotheses = structured.get("user_hypotheses") or []
        hypotheses = [
            {
                "hypothesis_id": item["hypothesis_id"],
                "statement": item["statement"],
                "status": item["status"],
                "evidence_level": item.get("current_evidence_level", "E0"),
                "success_threshold": item.get("success_threshold"),
            }
            for item in raw_hypotheses
            if isinstance(item, dict)
        ]
        raw_personas = structured.get("personas") or []
        personas = [
            {
                "persona_id": item["persona_id"],
                "label": item["label"],
                "behavior_keys": item.get("behavior_keys"),
            }
            for item in raw_personas
            if isinstance(item, dict)
        ]
        simulated = structured.get("simulated_findings")
        task_results = simulated.get("task_test_matrix") if isinstance(simulated, dict) else None
        return {
            "task_id": str(report.get("task_id") or "unknown"),
            "project_id": str(manifest.get("project_id") or "unknown"),
            "product_version": str(manifest.get("product_version") or "unknown"),
            "product_tasks_hash": str(manifest.get("product_tasks_hash") or ""),
            "scoring_schema_version": manifest.get("scoring_schema_version"),
            "hypotheses": hypotheses,
            "personas_digest": personas or None,
            "experience_issue_ids": structured.get("experience_issue_ids") or None,
            "task_results": task_results,
        }

    @staticmethod
    def _evidence_input(row: Mapping[str, object], product_version_label: object) -> dict[str, object]:
        applicability = row["applicability"] if isinstance(row["applicability"], dict) else {}
        return {
            "evidence_id": str(row["id"]), "kind": row["kind"], "tier": row["claimed_tier"],
            "source_tier": row["source_tier"], "source": row["source"],
            "timestamp": _timestamp(row["observed_at"]).isoformat(),
            "expiry": _timestamp(row["expires_at"]).isoformat() if row["expires_at"] else None,
            "sample_size": row["sample_size"], "observation": row["aggregate_observation"],
            "applies_to_segment": row["segment"], "applies_to_product_version": str(product_version_label),
            "version_stable": applicability.get("version_stable"), "stable_reason": applicability.get("stable_reason"),
            "applies_to_persona_ids": applicability.get("persona_ids"),
            "supporting_claims": row["supporting_claim_refs"] or None,
            "contradicts_claims": row["contradicting_claim_refs"] or None,
            "valid_for_dimensions": applicability.get("dimensions"),
        }

    @staticmethod
    def _validate_evidence(values: dict[str, object]) -> None:
        if values["kind"] not in {
            "interview", "survey", "usability_test", "review", "public_comment", "usage_data",
            "retention_data", "payment_record", "contract", "team_statement",
        }:
            raise ValueError("unsupported user evidence kind")
        if values["claimed_tier"] not in {"E0", "E1", "E2", "E3", "E4", "E5"}:
            raise ValueError("claimed_tier must be E0 through E5")
        if not isinstance(values["observed_at"], datetime):
            raise ValueError("observed_at must be an ISO timestamp")
        if values["expires_at"] is not None and not isinstance(values["expires_at"], datetime):
            raise ValueError("expires_at must be an ISO timestamp")
        for key in ("source", "aggregate_observation"):
            if not isinstance(values[key], str) or not str(values[key]).strip():
                raise ValueError(f"{key} is required")

    @staticmethod
    def _script_view(row: Mapping[str, object], task_hash: str) -> dict[str, object]:
        return {
            "script_id": str(row["id"]), "product_version_id": str(row["product_version_id"]),
            "revision": row["revision"], "sha256": row["sha256"],
            "product_tasks_hash": row.get("product_tasks_sha256") or task_hash,
            "task_count": row["task_count"],
        }

    @staticmethod
    def _evidence_view(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "user_evidence_id": str(row["id"]), "product_version_id": str(row["product_version_id"]),
            "kind": row["kind"], "claimed_tier": row["claimed_tier"], "sha256": row["sha256"],
        }

    def _script_task_hash(self, script: Mapping[str, object]) -> str:
        document = json.loads(self._objects.get_private(_string(script["object_key"]), max_bytes=2_000_000))
        return str(document["product_tasks_hash"])

    def _read_result_artifact(self, result: Mapping[str, object]) -> dict[str, object]:
        object_key = _string(result["object_key"])
        metadata = self._objects.head(object_key)
        if metadata is None or metadata.sha256 != result["sha256"]:
            raise ArtifactIntegrityError("user-validation object metadata does not match its durable reference")
        body = self._objects.get_private(object_key, max_bytes=8_000_000)
        if hashlib.sha256(body).hexdigest() != result["sha256"]:
            raise ArtifactIntegrityError("user-validation result hash does not match its durable reference")
        try:
            artifact = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError("user-validation result artifact is not valid JSON") from exc
        if not isinstance(artifact, dict):
            raise ArtifactIntegrityError("user-validation result artifact must be an object")
        return artifact

    @staticmethod
    def _presentation_metadata(report: Mapping[str, object]) -> dict[str, object] | None:
        structured = report.get("structured_output")
        if not isinstance(structured, dict):
            if str(report.get("status") or "").lower() in {"completed", "partial"}:
                raise ValueError("completed user-validation result lacks structured output")
            return None
        target = structured.get("target_user_definition")
        admitted = isinstance(target, dict) and target.get("admitted") is True
        required = str(report.get("status") or "").lower() in {"completed", "partial"} and admitted
        fields = {
            ("summary", "markdown"): "summary_report",
            ("summary", "html"): "summary_report_html",
            ("full", "markdown"): "full_report",
            ("full", "html"): "full_report_html",
        }
        values = {key: structured.get(field) for key, field in fields.items()}
        available = {key: isinstance(value, str) and bool(value.strip()) for key, value in values.items()}
        if required and not all(available.values()):
            raise ValueError("admitted completed or partial result requires all four report presentations")
        if not any(available.values()):
            return None
        if not all(available.values()):
            raise ValueError("user-validation presentations must be stored as one complete four-format set")
        if structured.get("human_report") != values[("summary", "markdown")]:
            raise ValueError("human_report must equal summary_report")
        if structured.get("human_report_html") != values[("summary", "html")]:
            raise ValueError("human_report_html must equal summary_report_html")
        metadata: dict[str, object] = {"version": _PRESENTATION_VERSION}
        for variant in ("summary", "full"):
            variant_metadata: dict[str, object] = {}
            for report_format in ("markdown", "html"):
                content = values[(variant, report_format)]
                if not isinstance(content, str):
                    raise ValueError("user-validation presentation content must be a string")
                if len(content.encode("utf-8")) > _MAX_REPORT_CONTENT_BYTES:
                    raise ReportTooLargeError("user-validation presentation exceeds one megabyte")
                variant_metadata[report_format] = {
                    "available": True,
                    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            metadata[variant] = variant_metadata
        return metadata

    @staticmethod
    def _step_id(response: Mapping[str, object]) -> str | None:
        step = response.get("step")
        return str(step.get("step_id")) if isinstance(step, dict) and step.get("step_id") else None

    @staticmethod
    def _execution_status(response: Mapping[str, object]) -> str:
        if response.get("status") == "awaiting_step":
            return "AWAITING_STEP"
        result = response.get("result")
        native_status = str(result.get("status") or "failed").upper() if isinstance(result, dict) else "FAILED"
        return native_status if native_status in {"BLOCKED", "FAILED"} else "COMPLETED"

    @staticmethod
    def _product_stage(value: object) -> str:
        normalized = str(value or "unknown").strip().lower()
        return normalized if normalized in {"unknown", "concept", "demo", "mvp", "productized", "scaled"} else "unknown"

    @staticmethod
    def _is_preliminary(report: dict[str, object]) -> bool:
        structured = report.get("structured_output")
        if not isinstance(structured, dict):
            return True
        evidence = structured.get("evidence_effect_ledger") or []
        return not any(
            isinstance(item, dict)
            and item.get("effective_tier") in {"E3", "E4", "E5"}
            and item.get("scope_valid") is True
            and item.get("product_version_valid") is True
            for item in evidence
        )

    def _object_size(self, object_key: str) -> int:
        metadata = self._objects.head(object_key)
        return int(metadata.size_bytes) if metadata is not None else 0

    @staticmethod
    def _audit_items(section: str, report: object, structured: object) -> list[object]:
        report_dict = report if isinstance(report, dict) else {}
        data = structured if isinstance(structured, dict) else {}
        if section == "summary":
            return [{
                "status": report_dict.get("status"), "result_summary": report_dict.get("result_summary"),
                "confidence": report_dict.get("confidence"), "risks": report_dict.get("risks"),
                "score": data.get("user_value_score"),
                "coverage": data.get("evidence_confidence"),
                "judgment": data.get("user_value_judgment"),
            }]
        keys = {
            "claims": ("user_hypotheses", "claims"),
            "evidence": ("evidence_cards", "evidence_effect_ledger", "evidence_level_summary"),
            "conflicts": ("conflicts", "integrity_diagnostics", "rejected_output"),
            "expiry": ("evidence_cards", "integrity_diagnostics", "flags"),
        }
        if section not in keys:
            raise ValueError("audit context section is invalid")
        items: list[object] = []
        for key in keys[section]:
            value = data.get(key)
            if isinstance(value, list):
                items.extend(value)
            elif value is not None:
                items.append({key: value})
        return items

    @staticmethod
    def _knowledge_rules(section: str) -> list[str]:
        rules = {
            "summary": ["KB-EVD-R02", "KB-EVD-G01", "KB-EVD-G02", "KB-EVD-D05"],
            "claims": ["KB-EVD-F02", "KB-EVD-S1", "KB-EVD-S3"],
            "evidence": ["KB-EVD-F01", "KB-EVD-F03", "KB-EVD-S2", "KB-EVD-G03"],
            "conflicts": ["KB-EVD-F05", "KB-EVD-S4", "KB-EVD-D02"],
            "expiry": ["KB-EVD-F04", "KB-EVD-S5", "KB-EVD-D04"],
        }
        return rules.get(section, [])

    def _needs_attention(
        self, actor: Actor, run_id: object, task_id: object, failure_class: str, reason: str
    ) -> None:
        now = datetime.now(UTC)
        with self._transaction(actor) as session:
            self._attention_in_session(session, actor.tenant_id, run_id, task_id, failure_class, reason, now=now)

    @staticmethod
    def _attention_in_session(
        session: Session,
        tenant_id: UUID,
        run_id: object,
        task_id: object,
        failure_class: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> None:
        moment = now or datetime.now(UTC)
        run_status = session.execute(select(evaluation_run.c.status).where(
            evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id,
        )).scalar_one_or_none()
        if run_status is None:
            return
        session.execute(update(task).where(
            task.c.tenant_id == tenant_id, task.c.id == task_id,
        ).values(status="NEEDS_ATTENTION", last_failure_class=failure_class, last_error=reason, updated_at=moment))
        session.execute(
            update(evaluation_run)
            .where(evaluation_run.c.tenant_id == tenant_id, evaluation_run.c.id == run_id)
            .values(
                status="NEEDS_ATTENTION",
                last_failure_class=failure_class,
                attention_reason=reason,
                updated_at=moment,
            )
        )
        session.execute(run_status_history.insert().values(
            id=uuid4(), tenant_id=tenant_id, run_id=run_id, from_status=run_status,
            to_status="NEEDS_ATTENTION", reason=reason, failure_class=failure_class, occurred_at=moment,
        ))

    def _transaction(self, actor: Actor) -> AbstractContextManager[Session]:
        return tenant_transaction(self._sessions, TenantScope(actor.tenant_id), actor_id=actor.actor_id)


__all__ = [
    "ArtifactIntegrityError",
    "ExecutionResponse",
    "IdempotencyConflictError",
    "ObjectWriteUnknownError",
    "PrivateObjectStore",
    "ReportTooLargeError",
    "UserValidationApplication",
]
